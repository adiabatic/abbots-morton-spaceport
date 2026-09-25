"""Tests for the memory-budget policy in `rebuild/tools/memory_budget.py`, for the widths derived from it, and for the `-n auto` hooks.

Most tests are pure functions over an invented machine, because `total_bytes`, `floor_bytes` and `fraction` are keyword parameters on every policy function. The probes are tested through their pure parsers over the checked-in samples under `rebuild/fixtures/memory_budget/`, and the two cgroup readers are pointed at those sample roots, so every container case runs on a laptop. The only test that compares a live reading with an outside figure checks `total_memory_bytes()` against `sysctl -n hw.memsize`, on Darwin only.

Three constants here are recorded measurements, kept as literals so that re-measuring a shipped constant cannot move the reproduction of an earlier width. `KERNEL_CONFIG_BYTES` is what one kernel configuration in flight cost when issues #46 and #85 were written; it does not follow `kernel_exec.DELTA_PEAK_BYTES`. `FONT_POOL_BYTES` is ten font-suite workers at the top of the 0.11–0.28 GB range the root `conftest.py` records beside `FONT_SUITE_WORKER_BYTES`; it does not follow that constant, which rounds up past the range. `ISSUE_RESERVE_FLOOR_BYTES` is the 4 GB reserve floor issue #85 stated its widths under; the shipped floor, `RESERVE_FLOOR_BYTES`, is 8 GB.

`TestTheWidthsAlreadyOnRecord` shows the formula reproducing widths that were measured independently of it, over an invented 32 GB machine. The shipped kernel width is derived from the running machine, so no test compares it with a fixed number. Instead the tests check that the shipped `DELTA_PEAK_BYTES` and `DEFAULT_MEMO_BYTES` still fit the whole delta wave on the 48 GiB machine and three deltas on the 32 GiB machine when the build runs alone; `rebuild/test_artifact_cycle.py` checks the same widths beside the cycle's pytest pool. `TestWhatDashNAutoResolvesTo` drives the repository's two `pytest_xdist_auto_num_workers` hooks, and `TestTheHandRunDefaults` checks that each width a hand run gets without naming one is still derived from the machine.

Nothing here reads a live build artifact, so the module is in the contracts lane. The audit guard in `rebuild/conftest.py` fails any contracts test that reads `rebuild/out/`, `tmp/`, `var/`, or a root `verdicts-*` store.
"""

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from rebuild.pipeline.kernel_exec import DEFAULT_MEMO_BYTES, DELTA_PEAK_BYTES
from rebuild.tools import memory_budget

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES = REPO_ROOT / "rebuild" / "fixtures" / "memory_budget"

KERNEL_CONFIG_BYTES = 9_000_000_000
FONT_POOL_BYTES = 2_800_000_000
ISSUE_RESERVE_FLOOR_BYTES = 4_000_000_000
BOX_32_GIB = 34_359_738_368
BOX_48_GIB = 51_539_607_552
BOX_32_GB = 32_000_000_000
BOX_64_GB = 64_000_000_000
BOX_1_TB = 1_000_000_000_000
SPELLINGS_OF_32_GB = (BOX_32_GIB, BOX_32_GB)

BOX_SIZES = (
    4_000_000_000,
    8_000_000_000,
    16_000_000_000,
    BOX_32_GB,
    BOX_32_GIB,
    48_000_000_000,
    64_000_000_000,
    96_000_000_000,
    128_000_000_000,
    192_000_000_000,
    256_000_000_000,
    512_000_000_000,
)

CLAUSE = re.compile(
    r"^(?P<count>\d+) at (?P<per_unit>[\d.]+) GB each out of (?P<total>[\d.]+) GB total"
    r", less a reserve of (?P<reserve>[\d.]+) GB"
    r"(?:, less (?P<coresident>[\d.]+) GB co-resident)?"
)


@pytest.fixture(autouse=True)
def _no_inherited_override(monkeypatch: pytest.MonkeyPatch):
    """Clear both environment variables this module reads, so that a value exported in the shell that started pytest cannot decide a width. A test that is about one of them sets it again. An exported `PYTEST_XDIST_AUTO_NUM_WORKERS` would make every hook return that number and let the hook tests pass for the wrong reason."""
    monkeypatch.delenv("AMS_TOTAL_MEMORY_BYTES", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)


def _sample(*parts: str) -> str:
    return SAMPLES.joinpath(*parts).read_text(encoding="utf-8")


def _defined_public_names() -> set[str]:
    """Return the public names `memory_budget` defines itself, leaving out imports. Modules (`os`, `re`, `sys`) are dropped by type, and `Path`, `format_gb` and `annotations` by their `__module__`. An int, a float or a dict has no `__module__`, so a policy constant stays in the set, and so would a mapping of per-unit costs."""
    home = memory_budget.__name__
    return {
        name
        for name, value in vars(memory_budget).items()
        if not name.startswith("_")
        and not isinstance(value, ModuleType)
        and getattr(value, "__module__", home) == home
    }


def _loaded_conftest(pytestconfig: pytest.Config, path: Path) -> ModuleType:
    """Return the conftest module pytest loaded from `path`, looked up on the plugin manager, which registers each conftest under its absolute path. The tests need the live object, not an import: `import rebuild.conftest` would execute a second copy beside the one pytest loaded and installed the audit hook in, and the root `conftest.py` cannot be imported by name in a run collected under rebuild/, where the plain `conftest` in `sys.modules` is rebuild/'s own."""
    plugin = pytestconfig.pluginmanager.get_plugin(str(path))
    assert isinstance(plugin, ModuleType), f"pytest has not loaded {path} as a plugin"
    return plugin


class _StubConfig:
    """A stand-in for the `Config` the width hooks receive. The rebuild hook reads only `--lane`, and the root hook reads nothing from its config, so the argv paths a test passes only label the kind of run. It is stubbed because building a real `Config` over an invented argv would load this repository's conftests a second time just to register the `--lane` option."""

    def __init__(self, *args: str, lane: str = "all") -> None:
        self.args = list(args)
        self.invocation_params = SimpleNamespace(dir=REPO_ROOT)
        self._lane = lane

    def getoption(self, name: str, default: object = None) -> object:
        assert name == "lane", f"a width hook asked for an option this stub does not carry: {name}"
        return self._lane


class _ParserBuilt(Exception):
    """Raised by the spy in `_parser_built_by` to stop a tool's `main` as soon as its parser is complete, before it does any work."""


def _parser_built_by(main: Callable[[list[str]], object]) -> argparse.ArgumentParser:
    """Return the parser a tool's `main` builds, by running `main` until it calls `parse_args`. The test reads the parser the tool ships, not a copy assembled here. This is used instead of hoisting a `build_parser()` out of the two tools it serves, because both files are hashed into build stamps: `rebuild/pipeline/run_m1.py` is in `fingerprint.pipeline_code_paths` and `rebuild/review/build.py` in `fingerprint.review_code_paths`. A one-line hoist in either would make a stamped artifact stale and force its rebuild, and would prove nothing more about the default than reading it here does. Tools whose files no stamp covers expose a `build_parser()` instead."""
    captured: list[argparse.ArgumentParser] = []

    def spy(parser: argparse.ArgumentParser, args=None, namespace=None) -> None:
        captured.append(parser)
        raise _ParserBuilt

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(argparse.ArgumentParser, "parse_args", spy)
        with pytest.raises(_ParserBuilt):
            main([])
    return captured[-1]


class TestTheWidthsAlreadyOnRecord:
    @pytest.mark.parametrize("total", SPELLINGS_OF_32_GB)
    def test_the_formula_lands_on_the_solo_kernel_width_issue_46_measured(self, total: int):
        """Sub-issue #46 ran the fan-out at widths 1, 2, 3 and 6 on a 10-core 32 GB Darwin machine and concluded that the solo width there "is about 3". That measurement did not use this module, and this module was not tuned toward it: the divisor is the recorded cost of one configuration in flight and the floor is the one issue #85 stated. Both readings of "32 GB" are tested, so the result does not depend on the unit convention."""
        assert (
            memory_budget.how_many_fit(
                KERNEL_CONFIG_BYTES, total_bytes=total, floor_bytes=ISSUE_RESERVE_FLOOR_BYTES
            )
            == 3
        )

    @pytest.mark.parametrize("total", SPELLINGS_OF_32_GB)
    def test_subtracting_the_font_pool_lands_on_the_width_the_32_gb_box_shipped(self, total: int):
        """The second recorded fact: with the font suite's ten co-resident workers subtracted, because a cycle runs the fan-out beside a pytest pool, the same formula returns 2 on the same 32 GB machine. `kernel_threads_budget` in `rebuild/tools/artifact_cycle.py` makes this subtraction in the shipped code, estimating the pool at `MAKE_TEST_POOL_WORKERS` workers, not ten, so this test keeps the recorded fact with its own constants. The live `KERNEL_THREADS_DEFAULT` is not asserted, because it is the running machine's solo width and differs between machines."""
        assert (
            memory_budget.how_many_fit(
                KERNEL_CONFIG_BYTES,
                coresident_bytes=FONT_POOL_BYTES,
                total_bytes=total,
                floor_bytes=ISSUE_RESERVE_FLOOR_BYTES,
            )
            == 2
        )

    @pytest.mark.parametrize("total", SPELLINGS_OF_32_GB)
    def test_the_shipped_eight_gigabyte_floor_yields_the_width_the_32_gb_box_shipped(self, total: int):
        """The shipped policy reserves 8 GB instead of the issue's 4 GB, which costs the same 32 GB machine one configuration: with nothing subtracted it returns 2 where the issue's floor returned 3, and it still returns 2 with the font pool subtracted. That is why the floor is a parameter, so the test above can reproduce the issue's width."""
        assert memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, total_bytes=total) == 2
        assert (
            memory_budget.how_many_fit(
                KERNEL_CONFIG_BYTES, coresident_bytes=FONT_POOL_BYTES, total_bytes=total
            )
            == 2
        )

    @pytest.mark.parametrize("total, wanted", [(BOX_32_GIB, 3), (BOX_32_GB, 3)])
    def test_the_shipped_divisor_holds_the_32_gb_box_at_its_budgeted_width(
        self, total: int, wanted: int, monkeypatch: pytest.MonkeyPatch
    ):
        """The shipped kernel width subtracts `DEFAULT_MEMO_BYTES` from the machine before dividing the remaining budget by `DELTA_PEAK_BYTES`. Both readings of 32 GB fit three of the four delta configurations. The fleet machine is the GiB one. Its 34.36 GB is 1.34 GB short of fitting a fourth delta (35.7 GB is the smallest total that fits four), and the decimal 32 GB is 3.7 GB short. Changing either constant moves these widths. The second assertion checks that `TABLE_BUILD_PEAK_BYTES` is at most the memo plus one `DELTA_PEAK_BYTES` per delta configuration, which is what a width equal to the delta count holds; at a width equal to the configuration count, the extra worker slot runs `default`'s fold after its memo file is written. `AMS_KERNEL_THREADS` is cleared first, because an exported width would decide the first assertion whatever the constants are."""
        from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
        from rebuild.pipeline.kernel_exec import TABLE_BUILD_PEAK_BYTES, kernel_threads_default

        monkeypatch.delenv("AMS_KERNEL_THREADS", raising=False)
        assert kernel_threads_default(total_bytes=total) == wanted
        assert TABLE_BUILD_PEAK_BYTES <= DEFAULT_MEMO_BYTES + DELTA_PEAK_BYTES * (len(SETTLEMENT_CONFIGS) - 1)

    def test_the_shipped_pair_seats_the_whole_delta_wave_on_the_48_gib_box(self, monkeypatch):
        """The criterion `DELTA_PEAK_BYTES` and `DEFAULT_MEMO_BYTES` are chosen against: on the fleet's 48 GiB machine the solo width covers every configuration past `default`, so the delta wave runs in one round with no trailing round of a single delta. A change to either constant that costs that machine its fourth delta fails here. The 32 GiB machine fits three of the four deltas, which the test above checks. `AMS_KERNEL_THREADS` is cleared first, because an exported width would satisfy the inequality whatever the constants are."""
        from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
        from rebuild.pipeline.kernel_exec import kernel_threads_default

        monkeypatch.delenv("AMS_KERNEL_THREADS", raising=False)
        assert kernel_threads_default(total_bytes=BOX_48_GIB) >= len(SETTLEMENT_CONFIGS) - 1

    @pytest.mark.parametrize("total", (BOX_32_GIB, BOX_48_GIB))
    def test_the_replay_divisor_seats_every_configuration_on_both_fleet_boxes(self, total: int, monkeypatch):
        """The criterion `REPLAY_PEAK_BYTES` is chosen against: on both fleet machines the string replay's memory-derived width covers every settlement configuration, so all texts replay in one round. The second assertion checks that a replay costs less than a delta, since a replay's engine holds a subset of what a delta holds through enumeration; a value at or above `DELTA_PEAK_BYTES` means the constant no longer measures the replay. `AMS_REPLAY_THREADS` is cleared first, for the same reason as in the delta-wave test."""
        from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
        from rebuild.pipeline.kernel_exec import REPLAY_PEAK_BYTES, replay_threads_default

        monkeypatch.delenv("AMS_REPLAY_THREADS", raising=False)
        assert replay_threads_default(total_bytes=total) >= len(SETTLEMENT_CONFIGS)
        assert 0 < REPLAY_PEAK_BYTES < DELTA_PEAK_BYTES

    def test_the_memo_term_is_smaller_in_kind_than_the_divisor(self):
        """`DEFAULT_MEMO_BYTES` covers `default`'s memo snapshots kept alive for the wave, stored as compact records with their pools, while `DELTA_PEAK_BYTES` covers a configuration enumerated from scratch and held through its memo write, so the memo term must be the smaller. Setting them equal would charge the wave a whole configuration for a snapshot."""
        assert 0 < DEFAULT_MEMO_BYTES < DELTA_PEAK_BYTES

    def test_the_shipped_surface_divisor_holds_the_32_gib_box_at_the_cap_by_division(self):
        """On the 10-core 32 GiB machine under a gated cycle, the surface build's width is `SURFACE_JOBS_CAP` because eight workers fit the memory budget, which is the claim the `SURFACE_WORKER_BYTES` comment makes for that machine. This is an upper bound on `SURFACE_WORKER_BYTES` and `SURFACE_PARENT_BYTES`: a change that makes eight workers exceed this machine's budget narrows the width below the cap and fails here. A width test cannot give a lower bound without an invented machine tuned to divide exactly, which every change to the constants would have to re-tune. A worker estimate below what a worker really holds is caught instead by the surface-worker row of `make job-costs`, against the pool records `rebuild/review/build.py` writes."""
        import rebuild.tools.artifact_cycle as ac

        assert (
            ac.surface_job_budget(skip_gates=False, ncores=10, total_bytes=BOX_32_GIB) == ac.SURFACE_JOBS_CAP
        )


class TestWhatDashNAutoResolvesTo:
    """Tests of the two `pytest_xdist_auto_num_workers` hooks, asserted at the widths they return, which nothing else in the repository checks. They fail if the rebuild hook stops deferring to the root hook or if the root hook returns anything but the cores. Neither hook reads memory, so the tests set `AMS_TOTAL_MEMORY_BYTES` to a small and a large machine to check that the answer does not move."""

    @pytest.fixture
    def lane_hook(self, pytestconfig: pytest.Config):
        return _loaded_conftest(pytestconfig, REPO_ROOT / "rebuild" / "conftest.py")

    @pytest.fixture
    def root_hook(self, pytestconfig: pytest.Config):
        return _loaded_conftest(pytestconfig, REPO_ROOT / "conftest.py")

    def test_the_contracts_lane_takes_every_core_and_no_memory_argument_narrows_it(
        self, lane_hook: ModuleType, monkeypatch: pytest.MonkeyPatch
    ):
        """No test in the rebuild suite reads a live artifact, so no worker holds a working set that needs a memory bound, and even a small machine gets every core this process may run on."""
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", "4000000000")
        answer = lane_hook.pytest_xdist_auto_num_workers(_StubConfig("rebuild/", lane="contracts"))
        assert answer == memory_budget.usable_cores()

    def test_a_run_that_names_no_lane_falls_through_to_the_root_conftest(self, lane_hook: ModuleType):
        """Without `--lane contracts` the rebuild hook returns None and the root conftest decides. A bare `uv run pytest rebuild/`, a single rebuild test file and a mixed collection all arrive as lane `all`."""
        assert lane_hook.pytest_xdist_auto_num_workers(_StubConfig("rebuild/")) is None

    def test_the_font_suite_takes_the_cores_whatever_the_box_has_to_say(
        self, root_hook: ModuleType, monkeypatch: pytest.MonkeyPatch
    ):
        """The root hook returns the cores for a font-suite run. A font-suite worker is small enough that the cores limit the pool before memory does, so the answer is right even on a machine too small for a memory-derived width."""
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", "4000000000")
        assert root_hook.pytest_xdist_auto_num_workers(_StubConfig("test/", "site/")) == (
            memory_budget.usable_cores()
        )

    @pytest.mark.parametrize("total", ["4000000000", str(BOX_1_TB)])
    def test_a_run_this_hook_cannot_narrow_takes_the_cores_whatever_the_box(
        self, root_hook: ModuleType, monkeypatch: pytest.MonkeyPatch, total: str
    ):
        """The root hook also returns the cores for a rebuild run: no rebuild worker reads a live artifact, so there is no per-worker cost to divide by, and a small machine and a large one both get the cores this process may run on."""
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", total)
        assert root_hook.pytest_xdist_auto_num_workers(_StubConfig("rebuild/")) == (
            memory_budget.usable_cores()
        )

    @pytest.mark.parametrize("lane", ["contracts", "all"])
    def test_the_environment_override_outranks_every_width_either_hook_would_choose(
        self, lane_hook: ModuleType, root_hook: ModuleType, monkeypatch: pytest.MonkeyPatch, lane: str
    ):
        """`PYTEST_XDIST_AUTO_NUM_WORKERS` overrides every width. It works in two steps: the rebuild hook returns None whatever the lane when the variable is set, so the root hook reads it, and the root hook returns it for a font run and a rebuild run alike."""
        monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "3")
        assert lane_hook.pytest_xdist_auto_num_workers(_StubConfig("rebuild/", lane=lane)) is None
        assert root_hook.pytest_xdist_auto_num_workers(_StubConfig("rebuild/")) == 3
        assert root_hook.pytest_xdist_auto_num_workers(_StubConfig("test/", "site/")) == 3


class TestTheHandRunDefaults:
    """The widths a hand run gets when it names none, each asserted against the function that derives it and never against a number, because the answer depends on the machine. A merge or refactor that drops a `default=` would put a width back to a serial 1 with no test failing and no visible regression until someone timed a hand run. The autouse fixture clears the memory and xdist overrides, so both sides of each equality are computed from the same machine inside the test."""

    def test_the_extraction_fans_out_to_the_width_its_own_module_resolved(self):
        """`SHARD_WORKERS_DEFAULT` is resolved once at import, as `KERNEL_THREADS_DEFAULT` is. The first equality checks that the CLI passes that constant through instead of a literal; the second checks that the constant still equals `usable_cores()`, which catches a revert to a checked-in width that the first equality would miss, since both of its sides change together. `_shard_workers_default`'s docstring says why this width is a core count and not a memory division."""
        from rebuild.baseline import cli, extract

        parsed = cli.build_parser().parse_args(["extract", "--all"])
        assert parsed.workers == extract.SHARD_WORKERS_DEFAULT == memory_budget.usable_cores()

    def test_the_shard_width_narrows_inside_a_cpu_quota_the_way_every_width_here_does(self):
        """The shard width is `usable_cores`, so it narrows inside a cgroup CPU quota. `_shard_workers_default` takes the filesystem root to read the quota under, so the test points it at the checked-in sample trees instead of a container."""
        from rebuild.baseline import extract

        host = os.process_cpu_count() or os.cpu_count() or 1
        assert extract._shard_workers_default(cgroup_root=SAMPLES / "container-v2") == min(host, 2)
        assert extract._shard_workers_default(cgroup_root=SAMPLES / "container-v1") == min(host, 2)
        assert extract._shard_workers_default(cgroup_root=SAMPLES / "no-such-box") == host

    def test_the_m1_driver_sweeps_at_the_budget_the_artifact_cycle_would_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """run_m1's `--jobs` is the width of the post-build sweeps. A run that names none takes `sweep_job_budget()`, the width the artifact cycle passes, so a hand run splits the oracle's tables into row ranges at the cycle's width. That width is the cores clamped by the memory division over `ORACLE_SHARD_BYTES`, with nothing subtracted for a co-resident pool. The parser's default is None, because `--conform-only` resolves an unstated width to the belt's budget (`conform_job_budget`) instead, so the test reads the width where `main` passes it to `run_gates_only`. The belt's budget is patched to differ from the oracle's, because on a machine where the two are equal a run given the belt's default would pass unnoticed. `--gates-only` with `--conform-only` still takes the oracle's width, because the gates-only branch runs first and runs no conformance sweep."""
        import rebuild.tools.artifact_cycle as ac
        from rebuild.pipeline import run_m1

        assert _parser_built_by(run_m1.main).parse_args([]).jobs is None
        sweep = ac.sweep_job_budget()
        monkeypatch.setattr(ac, "conform_job_budget", lambda **_: sweep + 1)
        handed: list[int] = []
        monkeypatch.setattr(
            run_m1, "run_gates_only", lambda *, out_dir, jobs, fresh_cache: handed.append(jobs)
        )
        run_m1.main(["--gates-only"])
        run_m1.main(["--gates-only", "--conform-only"])
        assert handed == [sweep, sweep]

    def test_the_surface_build_takes_the_unreserved_arm_of_its_own_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A hand run has no co-resident `make test` pool to leave cores or memory to, so the default is `surface_job_budget` with `skip_gates=True`. Where memory holds the derived width below the cap, a checked-in width equal to it would pass the first equality. So the test moves the machine to 1 TB, where memory cannot bind, and checks that the width becomes the cores clamped at `SURFACE_JOBS_CAP`. On both fleet machines the width is already at the cap, so there the move changes nothing."""
        import rebuild.tools.artifact_cycle as ac
        from rebuild.review import build

        assert _parser_built_by(build.main).parse_args([]).jobs == ac.surface_job_budget(skip_gates=True)
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", str(BOX_1_TB))
        widened = _parser_built_by(build.main).parse_args([]).jobs
        assert widened == ac.surface_job_budget(skip_gates=True)
        assert widened == min(memory_budget.usable_cores(), ac.SURFACE_JOBS_CAP)

    def test_the_signature_width_is_the_hand_runs_whole_box(self, monkeypatch: pytest.MonkeyPatch):
        """The signature width does not depend on memory: a signature worker is one comparator, so the hand run's default is the cores with `skip_gates=True`. Shrinking the machine until `--jobs` falls to one unit worker leaves the signature width unchanged, which a memory-derived width would not."""
        import rebuild.tools.artifact_cycle as ac
        from rebuild.review import build

        cores = memory_budget.usable_cores()
        args = _parser_built_by(build.main).parse_args([])
        assert args.signature_jobs == ac.signature_job_budget(skip_gates=True) == cores
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", "8000000000")
        narrowed = _parser_built_by(build.main).parse_args([])
        assert narrowed.jobs == 1
        assert narrowed.signature_jobs == cores


class TestTheFloorAtOne:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"total_bytes": 2_000_000_000},
            {"total_bytes": 8_000_000_000},
            {"total_bytes": BOX_32_GB, "cap": 0},
            {"total_bytes": BOX_32_GB, "cap": -4},
            {"total_bytes": BOX_32_GB, "coresident_bytes": 24_000_000_000},
            {"total_bytes": BOX_32_GB, "coresident_bytes": 1_000_000_000_000},
            {"total_bytes": 1},
        ],
    )
    def test_a_box_too_small_for_one_unit_answers_one_and_never_zero(self, kwargs: dict[str, int]):
        """A build that will not start on a small machine is worse than one that runs slowly, so every way of reaching a budget of nothing returns one instead of zero: a tiny machine, a cap of zero or less, a co-resident pool that uses up the budget, and a pool larger than the machine. A negative budget does not raise."""
        assert memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, **kwargs) == 1

    def test_a_per_unit_cost_larger_than_any_box_still_answers_one(self):
        assert memory_budget.how_many_fit(1_000_000_000_000_000, total_bytes=512_000_000_000) == 1

    def test_an_unmeasured_unit_answers_the_cap_or_one_and_never_divides_by_zero(self):
        """A per-unit cost of zero or less means the unit is unmeasured, so it gets no memory-derived width: the answer is the cap if there is one, and one otherwise."""
        assert memory_budget.how_many_fit(0, total_bytes=BOX_32_GB) == 1
        assert memory_budget.how_many_fit(0, total_bytes=BOX_32_GB, cap=6) == 6
        assert memory_budget.how_many_fit(-1, total_bytes=BOX_32_GB, cap=6) == 6
        assert memory_budget.how_many_fit(0, total_bytes=BOX_32_GB, cap=0) == 1


class TestNoInputWidensTheAnswerByAccident:
    """Every degenerate input produces a narrower width, and every input the signatures accept produces an int, because a width is passed to `range` or to an argv."""

    @pytest.mark.parametrize("total", SPELLINGS_OF_32_GB)
    def test_a_negative_co_resident_pool_subtracts_nothing_rather_than_adding(self, total: int):
        """A call site that computes a pool's footprint as a difference can reach a negative number, and subtracting it would give a budget larger than the machine. It is clamped at zero, so the width equals the width with no pool, and `describe_fit` prints no co-resident clause, since that clause appears only when something was subtracted."""
        unstated = memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, total_bytes=total)
        assert (
            memory_budget.how_many_fit(
                KERNEL_CONFIG_BYTES, coresident_bytes=-100_000_000_000, total_bytes=total
            )
            == unstated
        )
        assert memory_budget.describe_fit(
            KERNEL_CONFIG_BYTES, coresident_bytes=-100_000_000_000, total_bytes=total
        ) == memory_budget.describe_fit(KERNEL_CONFIG_BYTES, total_bytes=total)

    def test_a_byte_count_written_the_way_this_repo_writes_a_gigabyte_answers_an_int(self):
        """`peak_rss.py` writes a gigabyte as `1e9`, so a byte-count keyword is often a float. A float width fails far from its cause (`range` raises on it and an argv carries it as `-n 4.0`), so each byte count is truncated to an int on the way in."""
        width = memory_budget.how_many_fit(9e9, total_bytes=BOX_32_GB, floor_bytes=4e9)
        assert isinstance(width, int)
        assert width == memory_budget.how_many_fit(
            KERNEL_CONFIG_BYTES, total_bytes=BOX_32_GB, floor_bytes=ISSUE_RESERVE_FLOOR_BYTES
        )
        assert isinstance(memory_budget.os_reserve_bytes(total_bytes=BOX_32_GB, floor_bytes=8e9), int)
        assert "4.0" not in memory_budget.describe_fit(
            KERNEL_CONFIG_BYTES,
            total_bytes=BOX_32_GB,
            cap=4.0,  # pyright: ignore[reportArgumentType]
        )

    def test_a_cap_arrives_as_a_count_and_leaves_as_one(self):
        """The cap is a count, so its annotation stays `int` and pyright rejects a float at any call site in this tree. The coercion is for the bench harnesses `memory_budget`'s docstring mentions, which import it without type checking."""
        capped = memory_budget.how_many_fit(
            KERNEL_CONFIG_BYTES,
            total_bytes=512_000_000_000,
            cap=4.0,  # pyright: ignore[reportArgumentType]
        )
        assert isinstance(capped, int)
        assert capped == 4


class TestTheReserveAndCapShape:
    def test_the_sweep_straddles_the_crossover_so_both_arms_are_exercised(self):
        crossover = memory_budget.RESERVE_FLOOR_BYTES / memory_budget.RESERVE_FRACTION
        assert min(BOX_SIZES) < crossover < max(BOX_SIZES)

    @pytest.mark.parametrize("total", BOX_SIZES)
    def test_the_floor_binds_below_the_crossover_and_the_fraction_above_it(self, total: int):
        floor = memory_budget.RESERVE_FLOOR_BYTES
        fraction = memory_budget.RESERVE_FRACTION
        reserve = memory_budget.os_reserve_bytes(total_bytes=total)
        assert reserve == max(floor, int(total * fraction))
        if total < floor / fraction:
            assert reserve == floor
        else:
            assert reserve == int(total * fraction) > floor

    def test_the_floor_and_the_fraction_are_both_levers(self):
        """Both the floor and the fraction change the reserve, which lets a test reproduce an earlier policy's widths without fitting today's constants to them."""
        assert (
            memory_budget.os_reserve_bytes(total_bytes=BOX_32_GB, floor_bytes=ISSUE_RESERVE_FLOOR_BYTES)
            == 4_800_000_000
        )
        assert memory_budget.os_reserve_bytes(total_bytes=BOX_32_GB) == 8_000_000_000
        assert memory_budget.os_reserve_bytes(total_bytes=BOX_32_GB, fraction=0.5) == 16_000_000_000
        assert (
            memory_budget.os_reserve_bytes(
                total_bytes=BOX_32_GB, floor_bytes=ISSUE_RESERVE_FLOOR_BYTES, fraction=0.0
            )
            == ISSUE_RESERVE_FLOOR_BYTES
        )

    def test_the_count_never_falls_as_the_box_grows(self):
        totals = sorted(BOX_SIZES)
        counts = [memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, total_bytes=total) for total in totals]
        with_pool = [
            memory_budget.how_many_fit(
                KERNEL_CONFIG_BYTES, coresident_bytes=FONT_POOL_BYTES, total_bytes=total
            )
            for total in totals
        ]
        assert counts == sorted(counts)
        assert with_pool == sorted(with_pool)
        assert min(counts) >= 1 and min(with_pool) >= 1
        assert counts[0] == 1 and counts[-1] > counts[0]
        assert all(pooled <= alone for pooled, alone in zip(with_pool, counts))

    @pytest.mark.parametrize("total", BOX_SIZES)
    def test_the_cap_binds_when_it_is_lower_and_is_invisible_when_it_is_not(self, total: int):
        uncapped = memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, total_bytes=total)
        capped = memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, total_bytes=total, cap=4)
        assert capped == min(uncapped, 4)
        assert 1 <= capped <= 4
        assert memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, total_bytes=total, cap=10_000) == uncapped
        assert uncapped >= 1


class TestTheCgroupClamp:
    def test_a_v2_chain_binds_on_the_least_limit_along_the_walk_not_the_leafs(self):
        """The sample container's leaf scope sets 4 GB and an ancestor sets 2 GB. A reader that stopped at the leaf would return the looser figure, and the container would be OOM-killed at the tighter one."""
        assert memory_budget._cgroup_memory_limit_bytes(SAMPLES / "container-v2") == 2_000_000_000

    def test_a_v1_unlimited_sentinel_is_absent_and_the_containers_own_limit_binds(self):
        """`memory.limit_in_bytes` writes unlimited as a page-rounded 2**63-1, which parses as a valid int and would clamp nothing while appearing to."""
        assert memory_budget._cgroup_memory_limit_bytes(SAMPLES / "container-v1") == 2_147_483_648

    def test_memory_high_is_a_limit_too_and_not_only_memory_max(self, tmp_path: Path):
        """The checked-in v2 chain has a `memory.high`, but its tightest limit is a `memory.max`, so only a root whose sole limit is a `memory.high` shows that both v2 files are read."""
        (tmp_path / "proc" / "self").mkdir(parents=True)
        (tmp_path / "proc" / "self" / "cgroup").write_text("0::/only.slice\n", encoding="utf-8")
        only = tmp_path / "sys" / "fs" / "cgroup" / "only.slice"
        only.mkdir(parents=True)
        (only / "memory.max").write_text("max\n", encoding="utf-8")
        (only / "memory.high").write_text("1500000000\n", encoding="utf-8")
        assert memory_budget._cgroup_memory_limit_bytes(tmp_path) == 1_500_000_000

    def test_a_desktop_with_max_everywhere_clamps_nothing(self):
        assert memory_budget._cgroup_memory_limit_bytes(SAMPLES / "host-unlimited") is None
        assert memory_budget._cgroup_cpu_allowance(SAMPLES / "host-unlimited") is None

    def test_a_root_with_no_proc_self_cgroup_answers_none_at_the_first_open(self):
        """With no `/proc/self/cgroup` under the root, each reader returns None after one failed open and no walk, so both clamps cost almost nothing on Darwin."""
        assert memory_budget._cgroup_memory_limit_bytes(SAMPLES / "no-such-box") is None
        assert memory_budget._cgroup_cpu_allowance(SAMPLES / "no-such-box") is None

    def test_the_cpu_quota_clamp_reads_v2_and_v1_alike(self):
        """The v2 leaf sets two cores under an ancestor's `max 100000`, and the v1 container sets one and a half cores under a mount root whose quota is -1; both return two whole cores."""
        assert memory_budget._cgroup_cpu_allowance(SAMPLES / "container-v2") == 2
        assert memory_budget._cgroup_cpu_allowance(SAMPLES / "container-v1") == 2

    def test_usable_cores_takes_the_cgroup_quota_when_one_is_stated(self):
        """The CPU clamp is separate from the memory clamp because `os.process_cpu_count` reads the affinity mask on Linux but not the CFS quota, so a quota-limited container that was never pinned reports every core the host has."""
        host = os.process_cpu_count() or os.cpu_count() or 1
        assert memory_budget.usable_cores(SAMPLES / "container-v2") == min(host, 2)
        assert memory_budget.usable_cores(SAMPLES / "container-v1") == min(host, 2)
        assert memory_budget.usable_cores(SAMPLES / "host-unlimited") == memory_budget.usable_cores(
            SAMPLES / "no-such-box"
        )

    def test_the_memory_clamp_is_linux_only(self):
        """`sysconf` reads the host's memory inside a container, so the cgroup clamp is what makes the figure correct there. The clamp applies only on Linux, so Darwin pointed at the same sample tree still returns its own memory."""
        assert (
            memory_budget.total_memory_bytes(platform="linux", cgroup_root=SAMPLES / "container-v2")
            == 2_000_000_000
        )
        assert memory_budget.total_memory_bytes(
            platform="darwin", cgroup_root=SAMPLES / "container-v2"
        ) == memory_budget.total_memory_bytes(platform="darwin")

    def test_meminfo_is_the_linux_fallback_where_sysconf_cannot_answer(self, monkeypatch: pytest.MonkeyPatch):
        """On Linux, when `_sysconf_total_bytes` returns None, the probe falls back to `/proc/meminfo` and then to `_LAST_RESORT_TOTAL_BYTES`, which equals the shipped reserve floor, so a machine that cannot be probed has no budget and every width is one. Darwin never reads `/proc/meminfo`."""
        monkeypatch.setattr(memory_budget, "_sysconf_total_bytes", lambda: None)
        assert (
            memory_budget.total_memory_bytes(platform="linux", cgroup_root=SAMPLES / "host-unlimited")
            == 16_219_492 * 1024
        )
        assert (
            memory_budget.total_memory_bytes(platform="linux", cgroup_root=SAMPLES / "container-v2")
            == 2_000_000_000
        )
        assert (
            memory_budget.total_memory_bytes(platform="linux", cgroup_root=SAMPLES / "no-such-box")
            == memory_budget.RESERVE_FLOOR_BYTES
        )
        assert (
            memory_budget.total_memory_bytes(platform="darwin", cgroup_root=SAMPLES / "host-unlimited")
            == memory_budget.RESERVE_FLOOR_BYTES
        )
        assert (
            memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, total_bytes=memory_budget.RESERVE_FLOOR_BYTES)
            == 1
        )


class TestThePureParsers:
    def test_meminfo_reads_kib_and_answers_bytes(self):
        assert (
            memory_budget._parse_meminfo_total_bytes(_sample("container-v1", "proc", "meminfo"))
            == 32_770_272 * 1024
        )
        assert (
            memory_budget._parse_meminfo_total_bytes(_sample("container-v2", "proc", "meminfo"))
            == 65_805_864 * 1024
        )
        assert (
            memory_budget._parse_meminfo_total_bytes(_sample("host-unlimited", "proc", "meminfo"))
            == 16_219_492 * 1024
        )
        assert memory_budget._parse_meminfo_total_bytes("") is None
        assert memory_budget._parse_meminfo_total_bytes("MemFree: 812336 kB\n") is None

    def test_a_memory_limit_reads_max_and_the_v1_sentinel_as_absent(self):
        v2 = ("container-v2", "sys", "fs", "cgroup", "kubepods.slice")
        assert memory_budget._parse_memory_limit(_sample(*v2, "memory.max")) is None
        assert (
            memory_budget._parse_memory_limit(
                _sample(*v2, "kubepods-burstable.slice", "kubepods-burstable-pod9f2c.slice", "memory.max")
            )
            == 2_000_000_000
        )
        v1 = ("container-v1", "sys", "fs", "cgroup", "memory")
        assert memory_budget._parse_memory_limit(_sample(*v1, "memory.limit_in_bytes")) is None
        assert (
            memory_budget._parse_memory_limit(_sample(*v1, "docker", "3a7ecb1f9d2e", "memory.limit_in_bytes"))
            == 2_147_483_648
        )
        assert memory_budget._parse_memory_limit(str(2**63 - 1)) is None
        assert memory_budget._parse_memory_limit(str(2**62)) is None
        assert memory_budget._parse_memory_limit(str(2**62 - 1)) == 2**62 - 1
        assert memory_budget._parse_memory_limit("") is None
        assert memory_budget._parse_memory_limit("   \n") is None
        assert memory_budget._parse_memory_limit("plenty") is None
        assert memory_budget._parse_memory_limit("0") is None
        assert memory_budget._parse_memory_limit("-1") is None

    def test_cpu_max_reads_both_spellings_and_rounds_a_fractional_quota_up(self):
        v2 = ("container-v2", "sys", "fs", "cgroup", "kubepods.slice")
        assert memory_budget._parse_cpu_max(_sample(*v2, "cpu.max")) is None
        assert (
            memory_budget._parse_cpu_max(
                _sample(
                    *v2,
                    "kubepods-burstable.slice",
                    "kubepods-burstable-pod9f2c.slice",
                    "cri-containerd-3a7e.scope",
                    "cpu.max",
                )
            )
            == 2
        )
        assert memory_budget._parse_cpu_max("100000 100000") == 1
        assert memory_budget._parse_cpu_max("150000 100000") == 2
        assert memory_budget._parse_cpu_max("50000 100000") == 1
        assert memory_budget._parse_cpu_max("max") is None
        assert memory_budget._parse_cpu_max("") is None
        assert memory_budget._parse_cpu_max("plenty 100000") is None
        assert memory_budget._parse_cpu_max("200000 0") is None

    def test_a_cfs_quota_of_minus_one_is_absent_and_a_real_one_rounds_up(self):
        v1 = ("container-v1", "sys", "fs", "cgroup", "cpu,cpuacct")
        assert (
            memory_budget._parse_cpu_cfs_quota(
                _sample(*v1, "cpu.cfs_quota_us"), _sample(*v1, "cpu.cfs_period_us")
            )
            is None
        )
        assert (
            memory_budget._parse_cpu_cfs_quota(
                _sample(*v1, "docker", "3a7ecb1f9d2e", "cpu.cfs_quota_us"),
                _sample(*v1, "docker", "3a7ecb1f9d2e", "cpu.cfs_period_us"),
            )
            == 2
        )
        assert memory_budget._parse_cpu_cfs_quota("100000", "100000") == 1
        assert memory_budget._parse_cpu_cfs_quota("plenty", "100000") is None
        assert memory_budget._parse_cpu_cfs_quota("100000", "0") is None

    def test_proc_self_cgroup_maps_the_unified_line_and_every_v1_controller(self):
        unified = memory_budget._parse_proc_cgroup(_sample("container-v2", "proc", "self", "cgroup"))
        assert set(unified) == {""}
        assert unified[""].endswith("cri-containerd-3a7e.scope")
        legacy = memory_budget._parse_proc_cgroup(_sample("container-v1", "proc", "self", "cgroup"))
        assert legacy["memory"] == legacy["cpu"] == legacy["cpuacct"] == "/docker/3a7ecb1f9d2e"
        assert "" not in legacy
        assert memory_budget._parse_proc_cgroup("") == {}

    def test_the_walk_is_leaf_first_and_takes_in_the_mount_root(self):
        mount = Path("/sys/fs/cgroup")
        assert memory_budget._cgroup_dirs(mount, "/a/b") == [mount / "a" / "b", mount / "a", mount]
        assert memory_budget._cgroup_dirs(mount, "/") == [mount]


class TestTheEnvironmentOverride:
    def test_it_replaces_the_probe_and_outranks_even_the_cgroup_clamp(self, monkeypatch: pytest.MonkeyPatch):
        """The override lets a container state its own allowance, a large machine reproduce a small machine's widths, and a dry run print the same plan on every machine."""
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", "12345678901")
        assert memory_budget.total_memory_bytes() == 12_345_678_901
        assert (
            memory_budget.total_memory_bytes(platform="linux", cgroup_root=SAMPLES / "container-v2")
            == 12_345_678_901
        )

    def test_it_moves_the_box_and_never_the_policy(self, monkeypatch: pytest.MonkeyPatch):
        """The override replaces only the probed total: the same reserve is applied on top, and the floor and fraction parameters still decide the width."""
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", str(BOX_32_GB))
        assert memory_budget.os_reserve_bytes() == memory_budget.os_reserve_bytes(total_bytes=BOX_32_GB)
        assert memory_budget.os_reserve_bytes() == memory_budget.RESERVE_FLOOR_BYTES
        assert memory_budget.how_many_fit(KERNEL_CONFIG_BYTES) == memory_budget.how_many_fit(
            KERNEL_CONFIG_BYTES, total_bytes=BOX_32_GB
        )
        assert memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, floor_bytes=ISSUE_RESERVE_FLOOR_BYTES) == 3
        assert memory_budget.how_many_fit(KERNEL_CONFIG_BYTES) == 2
        assert memory_budget.describe_fit(KERNEL_CONFIG_BYTES).endswith(
            "out of 32.00 GB total, less a reserve of 8.00 GB"
        )

    @pytest.mark.parametrize("junk", ["", "   ", "not a number", "0", "-1", "32GB", "3.2e10", "32.0", "0x8"])
    def test_junk_in_it_is_ignored_rather_than_raised_on(self, monkeypatch: pytest.MonkeyPatch, junk: str):
        """A typo in this reproduction setting should leave the probe in charge instead of stopping a build, so only a bare decimal count of bytes is read and anything else is ignored."""
        probed = memory_budget.total_memory_bytes()
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", junk)
        assert memory_budget.total_memory_bytes() == probed


class TestTheLiveProbe:
    def test_the_box_answers_a_plausible_positive_figure_and_answers_it_twice(self):
        total = memory_budget.total_memory_bytes()
        assert 1_000_000_000 <= total <= 100_000_000_000_000
        assert memory_budget.total_memory_bytes() == total

    @pytest.mark.skipif(sys.platform != "darwin", reason="hw.memsize is the Darwin spelling of the probe")
    def test_the_portable_probe_is_byte_identical_to_hw_memsize_on_darwin(self):
        stated = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True
        ).stdout
        assert memory_budget.total_memory_bytes() == int(stated.strip())

    def test_usable_cores_is_at_least_one_and_never_more_than_the_box_offers(self):
        cores = memory_budget.usable_cores()
        assert cores >= 1
        assert cores <= (os.process_cpu_count() or os.cpu_count() or 1)

    def test_a_width_taken_off_the_live_box_is_startable_and_honors_its_cap(self):
        cores = memory_budget.usable_cores()
        assert memory_budget.how_many_fit(KERNEL_CONFIG_BYTES) >= 1
        assert 1 <= memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, cap=cores) <= cores


class TestDescribeFit:
    def test_the_clause_names_the_cost_the_box_the_reserve_and_the_co_resident_pool(self):
        clause = memory_budget.describe_fit(
            KERNEL_CONFIG_BYTES,
            coresident_bytes=FONT_POOL_BYTES,
            total_bytes=BOX_32_GIB,
            floor_bytes=ISSUE_RESERVE_FLOOR_BYTES,
        )
        assert clause == (
            "2 at 9.00 GB each out of 34.36 GB total, less a reserve of 5.15 GB, less 2.80 GB co-resident"
        )

    def test_the_clause_is_a_fragment_fit_for_a_plan_line(self):
        clause = memory_budget.describe_fit(KERNEL_CONFIG_BYTES, total_bytes=BOX_32_GIB, cap=8)
        assert "\n" not in clause
        assert clause == clause.strip()
        assert not clause.endswith(".")
        assert clause[0].isdigit()
        assert len(clause) < 160

    def test_a_reader_can_recompute_the_width_from_the_clause(self):
        """The clause exists so that a reader surprised by a width can check its derivation instead of trusting it."""
        clause = memory_budget.describe_fit(
            KERNEL_CONFIG_BYTES, coresident_bytes=FONT_POOL_BYTES, total_bytes=BOX_32_GB
        )
        stated = CLAUSE.match(clause)
        assert stated is not None
        budget = float(stated["total"]) - float(stated["reserve"]) - float(stated["coresident"])
        assert int(budget // float(stated["per_unit"])) == int(stated["count"])
        assert int(stated["count"]) == memory_budget.how_many_fit(
            KERNEL_CONFIG_BYTES, coresident_bytes=FONT_POOL_BYTES, total_bytes=BOX_32_GB
        )

    def test_the_optional_clauses_appear_only_when_they_apply(self):
        plain = memory_budget.describe_fit(KERNEL_CONFIG_BYTES, total_bytes=BOX_32_GB)
        assert "co-resident" not in plain and "capped at" not in plain and "floored" not in plain
        assert "capped at 8" in memory_budget.describe_fit(KERNEL_CONFIG_BYTES, total_bytes=BOX_32_GB, cap=8)
        assert "less 2.80 GB co-resident" in memory_budget.describe_fit(
            KERNEL_CONFIG_BYTES, coresident_bytes=FONT_POOL_BYTES, total_bytes=BOX_32_GB
        )
        assert memory_budget.describe_fit(KERNEL_CONFIG_BYTES, total_bytes=8_000_000_000) == (
            "1 at 9.00 GB each out of 8.00 GB total, less a reserve of 8.00 GB, floored at one"
        )

    def test_an_unmeasured_unit_says_so_instead_of_inventing_a_divisor(self):
        assert memory_budget.describe_fit(0, total_bytes=BOX_32_GB, cap=6) == (
            "6 at an unmeasured per-unit cost, so no memory-derived width, capped at 6"
        )

    def test_the_clause_and_the_count_never_disagree(self):
        for total in BOX_SIZES:
            clause = memory_budget.describe_fit(
                KERNEL_CONFIG_BYTES, coresident_bytes=FONT_POOL_BYTES, total_bytes=total, cap=6
            )
            count = memory_budget.how_many_fit(
                KERNEL_CONFIG_BYTES, coresident_bytes=FONT_POOL_BYTES, total_bytes=total, cap=6
            )
            assert clause.startswith(f"{count} at ")


def test_the_module_owns_the_arithmetic_and_holds_no_table_of_per_unit_costs():
    """Issue #85 names the risk: a central `UNIT_COSTS` mapping would hold the per-unit numbers while leaving the arguments for them at the call sites. Asserting the exact set of public names makes adding such a mapping fail this test."""
    assert _defined_public_names() == {
        "total_memory_bytes",
        "os_reserve_bytes",
        "usable_cores",
        "how_many_fit",
        "describe_fit",
        "RESERVE_FLOOR_BYTES",
        "RESERVE_FRACTION",
    }
    assert memory_budget.RESERVE_FLOOR_BYTES == 8_000_000_000
    assert memory_budget.RESERVE_FRACTION == 0.15
