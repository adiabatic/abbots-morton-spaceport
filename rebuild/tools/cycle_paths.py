"""Where the artifact cycle writes, and the two green-finish switches, in a leaf the rebuild suite patches without importing the cycle.

Every path here is one the cycle driver, a gate wrapper or an interactive entry point writes to or deletes: the green records each keyed stage skips on, the cycle summary, the run_m1 and conform summaries the driver unlinks before a spawn, and the build-log root every pass mints a run directory under. `rebuild/conftest.py`'s autouse `_redirect_cycle_writes` points each of them under `tmp_path` for every test in the rebuild suite, and every writer reads them off this module at call time — `cycle_paths.RUN_M1_GREEN`, never a copy bound at import or in a default argument — so a redirect made here reaches every write. The gate's exempt-prefix list sits beside them because the same conftest derives its forbidden trees from it.

The module imports nothing from the repo, and that is the reason it exists. The contracts lane folds both conftests' static import closures into every test's closure (`rebuild.tools.contracts_closure`), so a conftest that imports the cycle driver to patch these puts the whole of rebuild/pipeline/ and rebuild/review/ into every test's closure, and a pipeline edit then keeps no test off the lane. `rebuild/test_contracts_closure.py` holds the conftests to leaf imports.

`RETENTION_ENABLED` and `READINESS_ENABLED` switch off the two green-finish stages whose targets are resolved from the live tree rather than from a path here: the retention pass sweeps the root's carried exports and stashes and compacts the verdict journal, and the readiness checklist reads the served surface and the root autosave. The suite sets both False so a test reaching a green finish leaves the live repo alone; `_finish` in `artifact_cycle` reads both at call time, and a test asserting that it reaches either stage flips the switch back and patches the callable.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
M1_OUT = ROOT / "rebuild" / "out" / "m1"
CYCLE_SUMMARY = ROOT / "rebuild" / "out" / "cycle_summary.json"
MAKE_TEST_GREEN = ROOT / "rebuild" / "out" / "make-test-green.json"
PYRIGHT_GREEN = ROOT / "rebuild" / "out" / "pyright-green.json"
RUN_M1_GREEN = ROOT / "rebuild" / "out" / "run-m1-green.json"
CONFORM_GREEN = ROOT / "rebuild" / "out" / "conform-green.json"
DEEP_SWEEP_GREEN = ROOT / "rebuild" / "out" / "deep-sweep-green.json"
DEEP_REPLAY_GREEN = ROOT / "rebuild" / "out" / "deep-replay-green.json"
REBUILD_CONTRACTS_GREEN = ROOT / "rebuild" / "out" / "rebuild-contracts-green.json"
PLUMBING_GREEN = ROOT / "rebuild" / "out" / "plumbing-green.json"
# Where a pass keeps everything the terminal did not show, and how many such runs survive the green-finish retention pass. The root is a module constant so the rebuild suite can point it under a temp root — every other cycle write is redirected that way, and a run directory minted into the live repo by a test that drives main is the same kind of litter. Ten is a working week of passes: enough that a question about "the run before last" is still answerable, few enough that the pile stays a pile rather than an archive, and the whole of any one run is regenerable by running it again.
BUILD_LOGS_ROOT = ROOT / "var" / "build-logs"
BUILD_LOGS_KEEP = 10

M1_SUMMARY_FILES = {
    "pipeline": M1_OUT / "pipeline_summary.json",
    "manual_pins": M1_OUT / "manual_pins_summary.json",
    "oracle": M1_OUT / "oracle_summary.json",
}
CONFORM_SUMMARY = M1_OUT / "conform_summary.json"

REBUILD_GATE_EXEMPT_PREFIXES = (
    "rebuild/evidence/",
    "rebuild/review/jstests/",
    "rebuild/review-census-pins.json",
    "rebuild/m1-contact-allow.yaml",
)

RETENTION_ENABLED = True
READINESS_ENABLED = True
