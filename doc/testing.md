# Testing

Which gate answers for which kind of change, and what each gate refuses. Every rule here has an executable authority, named beside it; this file describes the shape and points at the authority rather than restating it.

## Which gate to run

| Change                                              | Gate                                                                       |
| --------------------------------------------------- | -------------------------------------------------------------------------- |
| Glyph data or code in the main tree                 | `make test`                                                                |
| Anything under `rebuild/`                           | `make test-rebuild`                                                        |
| Settlement semantics in `rebuild/kernel-rs/`        | `make kernel-gate`, then `make test-rebuild`                               |
| A rune or pipeline edit, to re-prove the tables     | `uv run python -m rebuild.pipeline.run_m1` (or a `make review-cycle` pass) |
| Commit time                                         | `make artifact-cycle`; `make review-cycle` when a sitting follows          |
| Is a sitting ready?                                 | `make verdict-ready`                                                       |
| Periodic, by hand or overnight                      | `make test-rebuild-slow`, `make conform-deep`                              |

Never single-thread a broad run; `doc/parallelism.md` carries the width rules. Detach any run that includes the heavy gates; `doc/running-long-steps.md` is the recipe.

## `make test`

- Self-skips in about a second when nothing it reads has changed since its last green run. `make_test_exempt` in `rebuild/tools/artifact_cycle.py` is the authority on what sits outside its closure; a change there cannot move it and is the rebuild suite's or the artifact cycle's to gate, so don't force a run for one.
- `make test FORCE=1` overrides the skip.
- Pyright runs inside the same invocation over the whole tree; `[tool.pyright] include` in `pyproject.toml` is the single authority on what it checks, so every invocation is a bare `uv run pyright` with no paths.

## `make test-rebuild`

- Runs the rebuild suite as one lane, contracts, with one green record (`rebuild/out/rebuild-contracts-green.json`, shared with the artifact cycle's gate:rebuild-contracts).
- No test under `rebuild/` reads live build output. `rebuild/conftest.py`'s audit guard fails one that reads a live tree (`rebuild/out/`, `tmp/`, the root verdict stores) with `ContractsLaneViolation`, so a new test reads against a synthetic root or `tmp_path`, and a claim about a live artifact belongs in the build that makes it.
- The suite reads only checked-in inputs, including the hermetic mini bundle under `rebuild/review/fixtures/mini/`, where the review surface's worked examples live, and runs at full width.
- The contracts lane runs narrower than its key says. Its green record carries a per-test input closure — what each test read, imported, and spawned, recorded by the same audit guard while the test ran — and when the key has moved, the lane runs only the tests whose closure the diff reaches, printing what it kept off (the artifact cycle's plan carries the same line for `gate:rebuild-contracts`). `rebuild/tools/contracts_closure.py` is the authority on what a closure holds and when a test may be kept off; its rule is that every doubt runs the test: a test with no recorded closure, a new or renamed id, a test that spawned a child the guard cannot follow (the two argued exceptions are a `git` command that reads only the object store, and the M1 kernel or its `cargo build`, whose closure is the crate's tracked sources), and every test at all when an input was added or removed or a global input such as `uv.lock` or the site fonts moved. `make test-rebuild FORCE=1` runs the whole lane and re-records every closure, and the green a narrowed run records covers the whole lane, because the tests it kept off passed against inputs whose bytes have not changed. A narrowed run still pays the lane's floor — every worker collects the whole suite before the selection deselects anything, and pyright's prelude finishes before the workers spawn — so the wall a selection saves is the test time above that floor; `make cycle-timings ARGS='--by-step'` is where to read it.
- Everything the build can prove about its own artifacts is proven in the build: the review surface's per-unit checks (`check_unit` and `check_shards` in `rebuild/review/build.py`), and the rule certificates the crate writes beside every table's rules, which `run_m1`'s witness stage settles and asserts before a glyph is minted (`run_rule_witnesses`; `rebuild/test_rule_witnesses.py` holds that machinery's contract on the mini fixture). Nothing here can go red on a stale artifact, because nothing here reads one; after a rune or pipeline edit, the M1 build is what re-proves the tables.
- This is also the pyright gate for `rebuild/`: `make_test_exempt` exempts that tree wholesale, so `make test` can never be its gate.
- The suite does not run the slow-marked tests; `make test-rebuild-slow` does, at the width its Makefile recipe states and argues.
- Codex's macOS sandbox blocks `sysctl -n hw.memsize`, so `rebuild/test_memory_budget.py::TestTheLiveProbe::test_the_portable_probe_is_byte_identical_to_hw_memsize_on_darwin` fails there even when the probe is correct; if it is the suite's only failure, rerun it outside the sandbox. Never weaken the probe or the test.

## Re-adjudicating without a build

The comparison-side inputs (the alias map, the divergence ledger, the contact allow-list, the kern sidecar, the oracle's module, the baselines) sit outside the tables' stamp, so a change to one of them needs no new enumeration:

```zsh
uv run python -m rebuild.pipeline.run_m1 --gates-only
```

That re-runs the defect gate, the Manual-pin gate and the oracle over the tables and font already on disk, and refuses a stale stamp. The artifact cycle takes that route itself when everything that moved since the last green build is comparison-side (`comparison_side_label` in `rebuild/tools/artifact_cycle.py` is the roster), and the green it records lets the next pass skip the build outright.

The oracle serves what it can from the per-row stores `rebuild/pipeline/oracle_cache.py` keeps beside the tables, each row carrying two verdicts under two keys: the settlement comparison under the row's rune keys, and the shaped-position verdict under those keys plus the font's per-family glyph digests, the kern sidecar and the oracle's own module. A ledger or alias edit serves both; a kern-sidecar or `oracle.py` edit serves the rows and re-shapes the positions; a rune edit re-derives only the rows reaching an edited family. The settle memo the oracle shares with `gate:conform` is keyed the same way, so a rune edit re-settles only the windows naming an edited family. The `[t] oracle` and `[t] settle_memo` lines in the run's log report what each pass served, retired and pruned; `oracle_summary.json` records `positions_served` beside `positions_compared`.

## The kernel's own gate

No cycle gate runs the crate's suite, so run `make kernel-gate` around any kernel-semantics change: it is the crate's fmt, clippy and test gate (`make kernel-check`), seconds once the crate is built. Among what it holds is the trace memo's byte identity (`rebuild/kernel-rs/src/memo.rs`): a configuration enumerated as a delta over `default`'s memo, and a build seeded from the previous build's memo files behind the edited runes, each file the bytes a from-scratch enumeration files, in the crate's own tests and through the binary in `tests/cli.rs`; `rebuild/test_kernel_exec.py` holds the same identity over the mini fixture through `run_m1.build_tables`, packed memos and stamp included. A build's own memo files land beside its tables as `rebuild/out/m1/memo-<config>.tsv.gz`, are read by the next build only while `run_m1.memo_stamp` still holds, and are never an input to any gate. What holds the crate's tables to the crate's engine is the string replay every M1 build runs right after its tables land (`run_m1.run_replay_strings` over the crate's `replay-strings` verb): the persisted rules walked over the string universe against a re-settle keyed on the raw window, whole-universe on a code or structure change and only over the texts naming an edited family on a rune edit, with `rebuild/out/m1/replay_summary.json` as the record the next build cuts its delta against. What holds the tables to the font is `gate:conform`, the HarfBuzz sweep of the compiled font against the same re-settle, with `make conform-deep` as its periodic deeper form; the ss10 overlay's arm of it is two letters deep behind read-back's isolation proof and never deepens. `doc/rebuild-design.md` §10 describes the tiers and §14.1 the crate.
