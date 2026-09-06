# Parallelism and memory budgets

Every fan-out width in this repository derives from the box it runs on: total physical memory, clamped by any cgroup limit and less a stated reserve, divided by a measured per-unit peak, capped by any non-memory bound, and floored at one. `rebuild/tools/memory_budget.py` owns that arithmetic and the policy behind it, including why free and available memory are never read. Each per-unit cost stays at its own call site as a named `*_BYTES` constant, in the docstring that argues the width, so a number and its argument are never in different files. `doc/fleet.md` records the machines this runs on and is the key for reading host-tagged output.

## The memory-bound fan-outs

Three fan-outs are memory-bound. Each names its cost where its width is resolved.

- The kernel's configuration fan-out. `CONFIG_PEAK_BYTES` in `rebuild/pipeline/kernel_exec.py` is what a live configuration holds until it has written its artifacts. `kernel_threads_default` resolves the solo width a bare `run_m1` gets (`KERNEL_THREADS_DEFAULT`); `kernel_threads_budget` in `rebuild/tools/artifact_cycle.py` resolves a cycle's, taking gate:make-test's pytest pool off the box before the division.
- The rebuild suite's validators lane. `VALIDATORS_WORKER_BYTES`, `VALIDATORS_LANE_CAP`, and `VALIDATORS_WORKERS` in `rebuild/conftest.py`, which is also the authority on the lane split. The root `conftest.py` restates the same per-worker figure as `HEAVIEST_WORKER_BYTES` and prices at it any run whose composition its hook cannot narrow (a lane-less `uv run pytest rebuild/`, a single rebuild test file, a mixed collection), since a worksteal pool cannot keep a validators test off any slot. The restatement is deliberate — importing a conftest executes it a second time — and `rebuild/test_memory_budget.py` holds the two literals equal.
- The review-surface build. In `rebuild/tools/artifact_cycle.py`, `SURFACE_PARENT_BYTES` is the parent that holds the whole corpus at any width and is subtracted from the box before the division, `SURFACE_WORKER_BYTES` is what one worker holds, `SURFACE_JOBS_CAP` is where the build stops scaling, and `surface_job_budget` is the one division over them. A box the pooled shape does not fit floors at one, which is the serial build rather than a refusal.

## Everything else

- The post-build sweeps are data-capped at one process per acceptance configuration (`sweep_job_budget` in `rebuild/tools/artifact_cycle.py`, which is all `run_m1._spawn_pool` will start).
- Font-suite workers (`FONT_SUITE_WORKER_BYTES` in the root `conftest.py`) are cheap enough that cores bind the pool before memory does, so their `-n auto` clamps to `memory_budget.usable_cores()` — the cores this process may actually run on, affinity mask and cgroup CPU quota included, which `os.cpu_count()` reads past. The contracts lane takes the same clamp because it reads no live artifact.
- `make test-rebuild-slow` states a narrow width in the Makefile rather than deriving one: the slow marker deselects nearly everything each worker has already collected, so a slot the selection cannot fill costs a collection and buys nothing.
- Under a cycle, gate:make-test's pool runs at the width the cycle states (`make_test_pool_width` and `MAKE_TEST_POOL_WORKERS` in `rebuild/tools/artifact_cycle.py`), passed through `PYTEST_XDIST_AUTO_NUM_WORKERS` on that one child, so the pool that runs is the pool the cycle reserved for.

## Overriding a width

A one-off run overrides in the environment, never by editing the checked-in values.

- `PYTEST_XDIST_AUTO_NUM_WORKERS` sets any `-n auto` pool.
- `AMS_KERNEL_THREADS` sets the kernel fan-out.
- `AMS_TOTAL_MEMORY_BYTES` overrides the memory probe rather than the policy, so a roomy box can reproduce a small one's widths. Junk in it is ignored rather than raised on.

The first two win ahead of everything derived.

## Measuring

- `make cycle-timings` summarizes what each step costs, host-tagged; `ARGS='--by-step'` compares step medians across machines. Read it before deciding a run is hung and before picking a watcher's timeout.
- `make job-costs` prints each checked-in per-unit constant beside what this host measured and the width each implies. `ARGS='--check'` exits nonzero when an observed peak outruns its constant; the artifact cycle runs that form every pass, and re-seeding the constant and committing it is the acceptance.
- The kernel's `--cache-census` flag on `enumerate` and `enumerate-configs` prints what each memo holds per configuration, and is the instrument for any memory argument about the kernel.

## Writing code against these limits

The Python and Rust here run against hard limits, not headroom. A routine that holds a little more per unit moves a per-unit peak, which narrows a fan-out, which lengthens every cycle after it — so a routine that is correct and simple and materially raises a peak is not done.

- Think about the working set before the algorithm: what the code holds live at its peak, for how long, and whether that is the whole corpus, one configuration, one unit, or one row. Stream where a list would materialize; release a large structure after its last reader rather than at function exit; prefer one folding pass to several that each re-read.
- Price the hot path in the units it runs at. A cost that is negligible per call becomes the build when the call runs per window, per candidate, or per row. Move invariant work out of the loop, memoize on the key the caller actually varies, and avoid intermediate strings, dicts, or `Vec`s the next line only takes apart.
- In Python, prefer the idiom that stays in C: comprehensions, `dict` and `set` lookups, `bytes` and `memoryview`, tuple keys, and the standard library's compiled paths. When a Python-side pass is still the bottleneck, the answer is usually the crate, not a cleverer loop.
- In Rust, mind allocation and cloning as much as asymptotics: borrow where you would `.clone()`, size a `Vec` or map up front when the count is known, keep per-entry structures flat, and prefer iterators that fuse over collecting between stages.
- A change that moves a per-unit peak revises the `*_BYTES` constant and the docstring that argues it at the same call site, because nothing else checks the widths derived from it. A performance change with no measurement behind it is a guess. Clarity wins ties.
