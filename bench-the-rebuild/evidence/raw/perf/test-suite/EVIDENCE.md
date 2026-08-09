# test-suite perf evidence index

Machine: Apple M4 Pro, 12 logical (8P+4E), macOS 26.6.1, CPython 3.14.6, pytest-xdist.
Repo at 704bd210, working tree clean before and after. No tracked file written; no verdict store,
no `rebuild/out/review/` rebuild, no green record written (raw `uv run pytest` used instead of the
`make test` / `make test-rebuild` gate wrappers, which are the only things that write green records).

All wall-clock numbers are contention-suspect (other agents may have been running). CPU-seconds
(`resource.getrusage` RUSAGE_CHILDREN), cProfile fractions and `/usr/bin/sample` stack fractions are
the contention-robust figures.

## Harness scripts (mine, scratch only)

- `runtime_wrap.py` — wall + child CPU (rusage) around any command; appends to `timings.ndjson`.
- `shapecount_plugin.py` — pytest plugin: wraps `uharfbuzz.shape`, records per test the shape-call
  count, the CPU inside shape, and total CPU (`time.process_time`). One NDJSON per xdist worker.
- `agg_shapecounts.py`, `agg_junit.py` — aggregators.
- `probe_worker_startup.py` — per-phase cost of what one worker pays before its first shaping test.
- `profile_sweep_shard.py`, `sweep_loop.py` — the dominant calt sweep, isolated.
- `profile_rule_witnesses.py`, `time_rule_witnesses.py` — the dominant rebuild-suite test, isolated.
- `scaling.sh` — xdist scaling curve on a fixed 92-test slice.

## Raw evidence

| file | what it holds |
| --- | --- |
| `timings.ndjson` | wall + child CPU for every whole-command run |
| `collect-main.txt` / `collect-rebuild.txt` | `--co -q` collection listings (6753 / 1162 tests) |
| `main-run.txt` | `make test`-equivalent run, `--durations=40` |
| `main-junit.xml` / `main-agg.txt` | per-test wall, aggregated by module and function |
| `main-run-instrumented.txt`, `shapecounts-main/`, `main-shapecounts-agg.txt` | per-test CPU + shape counts, 12 workers |
| `worker-startup.json` | per-phase per-worker startup cost |
| `sweep-shard-cprofile.txt`, `sweep-shard.prof` | cProfile of one calt sweep shard |
| `sample-sweep.txt` | `/usr/bin/sample` 25 s @ 1 ms of the calt sweep (native vs interpreter split) |
| `sweep-loop.txt`, `sweep-loop2.txt` | 20 shards, µs per shape |
| `pyright-run1.txt`, `pyright-run2.json` | `/usr/bin/time -p uv run pyright`, twice |
| `rebuild-run.txt`, `rebuild-junit.xml`, `rebuild-agg.txt` | rebuild suite, cold surface cache |
| `rebuild-run-warm.txt`, `rebuild-junit-warm.xml`, `rebuild-agg-warm.txt`, `shapecounts/`, `rebuild-shapecounts-agg.txt` | rebuild suite, warm surface cache, instrumented |
| `rule-witnesses-cprofile.txt`, `rule-witnesses.prof` | cProfile of `find_rule_witnesses[default]` |
| `rule-witnesses-split.json`, `rule-witnesses-split2.json` | unprofiled fixpoint-vs-hunt split |
| `sample-rule-witnesses.txt` | `/usr/bin/sample` 40 s of the fixpoint build (95.4 % CPython) |
| `scaling.ndjson`, `scaling-n*.txt` | xdist scaling curve, n = 1,2,3,6,9,12 + fixed prelude |

## State caveat on the rebuild numbers

On this tree the M1 enumeration under `rebuild/out/m1` is **not** stamped with the current sources,
so all six arms of `test_every_rule_has_a_witness` take the documented slow path and rebuild the
fixpoint in-process (see the warnings summary in `rebuild-run-warm.txt`). 20 tests fail, which is the
known stale-census-pin state, not something this investigation caused. The rebuild-suite figures
therefore describe the degraded path, not the steady state after a fresh `make artifact-cycle`.
