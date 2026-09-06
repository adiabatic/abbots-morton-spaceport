"""The memory-budget policy's own tests, and the reproduction of the two widths already on record (issue #63, sub-issue #85). Almost everything here is a pure function over an invented box, because `total_bytes`, `floor_bytes` and `fraction` are keywords on every policy function rather than module lookups; only the handful of live-probe tests touch the host, and those assert properties — positive, plausible, at least one core — with the single exception the issue permits, `total_memory_bytes()` against `sysctl -n hw.memsize` behind a Darwin guard. The probes are exercised the way the module split them to be exercised: pure parsers over the checked-in text under `rebuild/fixtures/memory_budget/`, and the two cgroup readers pointed at a sample filesystem root, so every container case is proven on a laptop. The three measured constants below come from the record rather than from solving for an answer — `KERNEL_CONFIG_BYTES` is the frozen reading of what one kernel configuration in flight cost when #46 and #85 were written, kept local and literal on purpose so a reproduction of a recorded width cannot move when the live `kernel_exec.CONFIG_PEAK_BYTES` is re-measured against a fresher cycle-timings journal; `FONT_POOL_BYTES` is ten font-suite workers at the 0.11-0.28 GB apiece the root conftest records beside its `pytest_xdist_auto_num_workers` hook, and keeps the top of that record rather than tracking the shipped `FONT_SUITE_WORKER_BYTES` that rounds up past it, for the same reason `KERNEL_CONFIG_BYTES` stays put; and `ISSUE_RESERVE_FLOOR_BYTES` is the 4 GB floor issue #85 stated its two facts under, passed explicitly because the shipped floor is now 8 GB. That the formula reproduces both facts over an invented 32 GB box is the whole claim: the policy is shown reproducing measurements taken independently of it, not fitted to them. The shipped default stopped being a witness the moment it became derived — it is the running box's width now, so no assertion here may sit it on the right-hand side of an equals sign; what is checked forward instead is that the shipped `CONFIG_PEAK_BYTES` still holds that 32 GB box at the width it shipped, which is the one comparison that can still fail for a reason worth knowing about. Two things checked here are not about the policy at all but about the call sites the policy could not reach on its own. What the repo's two `pytest_xdist_auto_num_workers` hooks actually answer, which nothing else in the tree asserts at all: the hooks are taken off the live plugin objects pytest loaded rather than off a second import of either file, driven with stub configs that carry only the argv and the lane they read, and walked across boxes with `AMS_TOTAL_MEMORY_BYTES` — so a lane losing its deliberate fall-through, or the fallback answering anything but the cores, fails here instead of quietly changing what `-n auto` means. The five defaults a hand run gets when it names no width (issue #101), each asserted at the shape it resolves to rather than at a number, for the reason nothing else here names one either — the answer is this box's, and another box's is legitimately different — so what is pinned is that a default is still derived at all, and a silent revert of one to the serial 1 it was before #89 fails here instead of waiting for someone to time a hand run and wonder. Three of the five hand their parser over from a `build_parser()` hoisted out of their own `main`; the two whose files sit inside a fingerprint closure are read off the parser their `main` built, through a spy on `parse_args`, because a hoist there would stale a stamped artifact and buy a rebuild of it. Nothing here reads a live build artifact, so the whole module is contracts-lane — the audit guard in `rebuild/conftest.py` is what keeps it there, by failing any contracts item that reads `rebuild/out/`, `tmp/`, or a root `verdicts-*` store."""

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from rebuild.pipeline.kernel_exec import CONFIG_PEAK_BYTES
from rebuild.tools import memory_budget

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES = REPO_ROOT / "rebuild" / "fixtures" / "memory_budget"

KERNEL_CONFIG_BYTES = 9_000_000_000
FONT_POOL_BYTES = 2_800_000_000
ISSUE_RESERVE_FLOOR_BYTES = 4_000_000_000
BOX_32_GIB = 34_359_738_368
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
    """Every assertion here is about the box and the widths taken off it, not about whatever the shell that started pytest had to say on either subject, so both environment variables this module reads are cleared before each test and set back only by the tests whose subject they are. `PYTEST_XDIST_AUTO_NUM_WORKERS` matters as much as the memory override does: a developer who exported it to widen this very run would otherwise see every hook answer collapse to their number and the assertions below pass for the wrong reason."""
    monkeypatch.delenv("AMS_TOTAL_MEMORY_BYTES", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)


def _sample(*parts: str) -> str:
    return SAMPLES.joinpath(*parts).read_text(encoding="utf-8")


def _defined_public_names() -> set[str]:
    """Every public name `memory_budget` itself defines, imports filtered out by the module each value calls home: `os`, `re` and `sys` are modules, `Path`, `format_gb` and `annotations` report a home elsewhere, and an int or a float reports no home at all — so a policy constant stays visible, and so would a mapping of per-unit costs."""
    home = memory_budget.__name__
    return {
        name
        for name, value in vars(memory_budget).items()
        if not name.startswith("_")
        and not isinstance(value, ModuleType)
        and getattr(value, "__module__", home) == home
    }


def _loaded_conftest(pytestconfig: pytest.Config, path: Path) -> ModuleType:
    """The conftest pytest itself loaded from `path`, taken off the plugin manager, which registers every conftest under its own absolute path as the plugin name. Reaching for the live object that way rather than importing it is what makes an assertion here about the module that actually answers: `import rebuild.conftest` executes a second copy beside the one pytest has loaded and armed the audit hook in, and the root `conftest.py` is not importable under any name at all from a run collected under rebuild/, where the plain `conftest` in `sys.modules` is this suite's own."""
    plugin = pytestconfig.pluginmanager.get_plugin(str(path))
    assert isinstance(plugin, ModuleType), f"pytest has not loaded {path} as a plugin"
    return plugin


class _StubConfig:
    """The two things the width hooks ask a `Config` for and nothing else: the argv paths a run collected, which is how the root hook tells a font-only run from every other kind, and the `--lane` this suite's own option carries. Stubbed rather than built, because a real `Config` over an invented argv would have to load this repo's conftests a second time just to have that option registered."""

    def __init__(self, *args: str, lane: str = "all") -> None:
        self.args = list(args)
        self.invocation_params = SimpleNamespace(dir=REPO_ROOT)
        self._lane = lane

    def getoption(self, name: str, default: object = None) -> object:
        assert name == "lane", f"a width hook asked for an option this stub does not carry: {name}"
        return self._lane


class _ParserBuilt(Exception):
    """Raised out of the spy below to stop a tool's own `main` the instant its parser is complete and before it does any of the work that parser was going to direct."""


def _parser_built_by(main: Callable[[list[str]], object]) -> argparse.ArgumentParser:
    """The parser a tool's own `main` built, taken by letting `main` run until it calls `parse_args` and no further — so what is read is the argv-facing object the tool actually ships, never a second one assembled here that could agree with it by accident. Reaching for a default this way rather than hoisting a `build_parser()` out of the two tools it is used on is a deliberate trade about stamps rather than about taste: `rebuild/pipeline/run_m1.py` sits inside `fingerprint.pipeline_code_paths`, whose hash stamps every serialized window enumeration on disk, and `rebuild/review/build.py` inside `review_code_paths`, whose hash the review surface's manifest carries — so a one-line hoist in either would stale a stamped artifact and cost a rebuild of it to get this suite green again, while proving nothing about the default that reading it here does not. The three tools whose files sit under no stamp are hoisted instead, and this is not used on them."""
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
        """Sub-issue #46 ran the fan-out at widths 1, 2, 3 and 6 on a 10-core 32 GB Darwin box and concluded the solo width there "is about 3". Nothing in that measurement passed through this module, and nothing in this module was tuned toward it: the divisor is the recorded cost of one configuration in flight and the floor is the one issue #85 wrote, so landing on 3 is a reproduction. Both readings of "32 GB" are asserted, so the reproduction does not rest on a unit convention."""
        assert (
            memory_budget.how_many_fit(
                KERNEL_CONFIG_BYTES, total_bytes=total, floor_bytes=ISSUE_RESERVE_FLOOR_BYTES
            )
            == 3
        )

    @pytest.mark.parametrize("total", SPELLINGS_OF_32_GB)
    def test_subtracting_the_font_pool_lands_on_the_width_the_32_gb_box_shipped(self, total: int):
        """The second recorded fact: subtract the font suite's ten co-resident workers, because a cycle runs the fan-out beside a pytest pool rather than alone, and the same formula answers 2 on the same invented 32 GB box rather than #46's solo 3. Since #90 that argument is arithmetic rather than prose — `kernel_threads_budget` in `rebuild/tools/artifact_cycle.py` makes the subtraction itself, pricing the pool at the two workers a cycle now holds gate:make-test to rather than this measurement's ten — so what this test keeps is the recorded fact, priced by its own constants, not the shipped reservation. The live `KERNEL_THREADS_DEFAULT` is deliberately not asserted here: it is the running box's solo width now, and a box of another size legitimately answers something else."""
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
        """The policy this repo actually ships reserves 8 GB rather than the issue's 4, which costs the same invented 32 GB box a whole configuration: with nothing subtracted it answers 2 where the issue's floor answered 3, and it still answers 2 with the font pool subtracted. So the shipped floor does not contradict what #46 measured — it only declines to reproduce that solo 3, which is why the floor is a parameter and the reproduction above can still pass the issue's."""
        assert memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, total_bytes=total) == 2
        assert (
            memory_budget.how_many_fit(
                KERNEL_CONFIG_BYTES, coresident_bytes=FONT_POOL_BYTES, total_bytes=total
            )
            == 2
        )

    @pytest.mark.parametrize("total", SPELLINGS_OF_32_GB)
    def test_the_shipped_divisor_holds_the_32_gb_box_at_the_width_the_memo_levers_bought(self, total: int):
        """The forward direction, and the only comparison left that means anything once `KERNEL_THREADS_DEFAULT` is derived: the left side is the divisor this repo actually ships run through the policy this repo actually ships, against a box that is stated rather than probed, and the right side is the width the trace memo's probe-cascade lever of issue #168 bought that box — six configurations in flight, the whole acceptance set, where the memo-side levers of issue #105 bought four and #46 recorded two at the divisor before them. Re-measuring `CONFIG_PEAK_BYTES` is meant to move this number, and pinning it here is what makes a re-seed say so in the same commit rather than widen a box quietly."""
        assert memory_budget.how_many_fit(CONFIG_PEAK_BYTES, total_bytes=total) == 6

    def test_the_shipped_surface_divisor_narrows_the_32_gib_box_below_its_core_clamp(self):
        """The forward direction for the third width, against the box that reported it. The ten-core 32 GiB Mac that ran the 2026-08-27 full-fresh surface build got eight workers out of the core clamp this replaced — ten cores less gate:make-test's two, which met `SURFACE_JOBS_CAP` exactly — and the only reading anyone had of that build's footprint was a step peak, which maxes over the process tree instead of summing it and so could see the parent alone. Deriving the width instead is what puts the workers in the number. The assertion is an inequality because both surface constants are readings to keep current: re-seeding either is free as long as it does not hand this box back its eight."""
        import rebuild.tools.artifact_cycle as ac

        assert (
            ac.surface_job_budget(skip_gates=False, ncores=10, total_bytes=BOX_32_GIB) < ac.SURFACE_JOBS_CAP
        )


class TestWhatDashNAutoResolvesTo:
    """The two hooks, asserted at the answers they give rather than at the constants they read — the step nothing else in the repo takes, and the one that would notice a lane losing its fall-through or the fallback starting to divide again. Neither hook takes a keyword, so the box each reads is moved with `AMS_TOTAL_MEMORY_BYTES`, the probe override `memory_budget` documents, which is what lets a laptop watch a small box and a large one answer alike."""

    @pytest.fixture
    def lane_hook(self, pytestconfig: pytest.Config):
        return _loaded_conftest(pytestconfig, REPO_ROOT / "rebuild" / "conftest.py")

    @pytest.fixture
    def root_hook(self, pytestconfig: pytest.Config):
        return _loaded_conftest(pytestconfig, REPO_ROOT / "conftest.py")

    def test_the_contracts_lane_takes_every_core_and_no_memory_argument_narrows_it(
        self, lane_hook: ModuleType, monkeypatch: pytest.MonkeyPatch
    ):
        """Nothing in the suite reaches a live artifact, so nothing there holds a working set worth bounding: even a small box gets every core this process may run on."""
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", "4000000000")
        answer = lane_hook.pytest_xdist_auto_num_workers(_StubConfig("rebuild/", lane="contracts"))
        assert answer == memory_budget.usable_cores()

    def test_a_run_that_names_no_lane_falls_through_to_the_root_conftest(self, lane_hook: ModuleType):
        """The fall-through itself, which is load-bearing rather than incidental: a bare `uv run pytest rebuild/`, a single rebuild test file and a mixed collection all arrive here as lane `all`, and the root conftest is the one place every run's `-n auto` is answered, `PYTEST_XDIST_AUTO_NUM_WORKERS` included."""
        assert lane_hook.pytest_xdist_auto_num_workers(_StubConfig("rebuild/")) is None

    def test_the_font_suite_takes_the_cores_whatever_the_box_has_to_say(
        self, root_hook: ModuleType, monkeypatch: pytest.MonkeyPatch
    ):
        """A font-suite worker is small enough that the cores bind before its footprint ever could, so that branch is core-bound rather than derived and stays right on a box no memory argument would leave room on."""
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", "4000000000")
        assert root_hook.pytest_xdist_auto_num_workers(_StubConfig("test/", "site/")) == (
            memory_budget.usable_cores()
        )

    @pytest.mark.parametrize("total", ["4000000000", str(BOX_1_TB)])
    def test_a_run_this_hook_cannot_narrow_takes_the_cores_whatever_the_box(
        self, root_hook: ModuleType, monkeypatch: pytest.MonkeyPatch, total: str
    ):
        """The fallback for every run the hook cannot tell from a rebuild one: no rebuild worker holds a live artifact, so there is no per-worker cost to divide the box by, and a small box and a roomy one both get the cores this process may run on."""
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", total)
        assert root_hook.pytest_xdist_auto_num_workers(_StubConfig("rebuild/")) == (
            memory_budget.usable_cores()
        )

    @pytest.mark.parametrize("lane", ["contracts", "all"])
    def test_the_environment_override_outranks_every_width_either_hook_would_choose(
        self, lane_hook: ModuleType, root_hook: ModuleType, monkeypatch: pytest.MonkeyPatch, lane: str
    ):
        """The one promise the repo's Python guidance makes about all of this, and it holds by a two-step mechanism worth pinning: the lane hook answers None whichever lane it was given, precisely so the root hook is the one that reads the variable, and the root hook then answers it for a font run and a rebuild run alike."""
        monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "3")
        assert lane_hook.pytest_xdist_auto_num_workers(_StubConfig("rebuild/", lane=lane)) is None
        assert root_hook.pytest_xdist_auto_num_workers(_StubConfig("rebuild/")) == 3
        assert root_hook.pytest_xdist_auto_num_workers(_StubConfig("test/", "site/")) == 3


class TestTheHandRunDefaults:
    """The widths a hand run gets when it names none, asserted at the shape each resolves to and never at a number, because the answer is this box's and another box's is legitimately different. What they guard against is the failure issue #101 names: a bad merge or a refactor that drops a `default=` puts one of these back to the serial 1 it was before #89, and nothing at all goes red — it reads as nobody's regression right up until someone times a hand run. The autouse fixture above is what makes each equality a single reading of one box rather than two: both sides are computed inside the test, with the probe override and the xdist override cleared."""

    def test_the_extraction_fans_out_to_the_width_its_own_module_resolved(self):
        """`SHARD_WORKERS_DEFAULT` is resolved once at import, exactly as `KERNEL_THREADS_DEFAULT` is, and the chain pins both links: the CLI hands that name through instead of a literal sitting beside it, and the name itself still derives from `usable_cores` rather than having been reverted to a checked-in width in place. The second equality is what catches the revert the first would wave through — both sides of `parsed.workers == SHARD_WORKERS_DEFAULT` move together when the constant is edited. Why a whole box's worth of these workers is safe is `_shard_workers_default`'s own docstring's argument."""
        from rebuild.baseline import cli, extract

        parsed = cli.build_parser().parse_args(["extract", "--all"])
        assert parsed.workers == extract.SHARD_WORKERS_DEFAULT == memory_budget.usable_cores()

    def test_the_shard_width_narrows_inside_a_cpu_quota_the_way_every_width_here_does(self):
        """The other half of that width's claim, and what the injection keyword buys: the default is `usable_cores` and therefore answers a cgroup CPU quota, which is provable on a laptop only once the function takes the filesystem root to read that quota under. Walked over the checked-in sample trees rather than over a container, so the container case is proven where the suite actually runs."""
        from rebuild.baseline import extract

        host = os.process_cpu_count() or os.cpu_count() or 1
        assert extract._shard_workers_default(cgroup_root=SAMPLES / "container-v2") == min(host, 2)
        assert extract._shard_workers_default(cgroup_root=SAMPLES / "container-v1") == min(host, 2)
        assert extract._shard_workers_default(cgroup_root=SAMPLES / "no-such-box") == host

    def test_the_m1_driver_sweeps_at_the_budget_the_artifact_cycle_would_pass(self):
        """run_m1's `--jobs` is the post-build sweeps' width, and the default is the same `sweep_job_budget()` the cycle already passes rather than a checked-in one, so a hand run walks the Manual-pin and oracle belts at the cycle's width instead of a configuration at a time. A CPU budget rather than a memory one, which is why nothing is subtracted from a box here; run_m1's memory ceiling is `--kernel-threads` and these jobs never reach it."""
        import rebuild.tools.artifact_cycle as ac
        from rebuild.pipeline import run_m1

        assert _parser_built_by(run_m1.main).parse_args([]).jobs == ac.sweep_job_budget()

    def test_the_surface_build_takes_the_unreserved_arm_of_its_own_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A hand run has no co-resident `make test` pool to leave cores or bytes to, which is the whole content of the `skip_gates=True` arm, so that arm is what the default is taken at. The second assertion is not decoration: on either box in the fleet memory binds this budget below the cap, where a revert to a checked-in width would pass the first assertion while saying nothing, so the box is moved to one with room and the answer is held at the other bound — the cap, which is where a roomy box stops. Both bounds get an assertion: which one binds is the design."""
        import rebuild.tools.artifact_cycle as ac
        from rebuild.review import build

        assert _parser_built_by(build.main).parse_args([]).jobs == ac.surface_job_budget(skip_gates=True)
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", str(BOX_1_TB))
        widened = _parser_built_by(build.main).parse_args([]).jobs
        assert widened == ac.surface_job_budget(skip_gates=True)
        assert widened == min(memory_budget.usable_cores(), ac.SURFACE_JOBS_CAP)


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
        """A build that refuses to start on a small machine is strictly worse than one that runs slowly, so every way of arriving at a budget of nothing — a tiny box, a cap of zero or less, a co-resident pool that eats the budget, a pool larger than the whole box — answers one rather than zero, and the negative budget never escapes as an exception."""
        assert memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, **kwargs) == 1

    def test_a_per_unit_cost_larger_than_any_box_still_answers_one(self):
        assert memory_budget.how_many_fit(1_000_000_000_000_000, total_bytes=512_000_000_000) == 1

    def test_an_unmeasured_unit_answers_the_cap_or_one_and_never_divides_by_zero(self):
        """Zero is not a per-unit cost, it is the absence of one, so it gets no memory-derived width at all: the cap answers if there is one, and one answers if there is not."""
        assert memory_budget.how_many_fit(0, total_bytes=BOX_32_GB) == 1
        assert memory_budget.how_many_fit(0, total_bytes=BOX_32_GB, cap=6) == 6
        assert memory_budget.how_many_fit(-1, total_bytes=BOX_32_GB, cap=6) == 6
        assert memory_budget.how_many_fit(0, total_bytes=BOX_32_GB, cap=0) == 1


class TestNoInputWidensTheAnswerByAccident:
    """Every degenerate input fails toward a narrower width, and every input the signatures admit answers a whole number, because a width leaves here for a `range` or an argv."""

    @pytest.mark.parametrize("total", SPELLINGS_OF_32_GB)
    def test_a_negative_co_resident_pool_subtracts_nothing_rather_than_adding(self, total: int):
        """The one input that could otherwise err high: a call site computing a pool's footprint as a difference reaches a negative, and subtracting it would hand back a budget larger than the box. It is clamped at zero, so it answers exactly what an unstated pool answers rather than more than the box can hold, and `describe_fit` — whose co-resident clause appears only when something was subtracted — stays honest by there being nothing to claim."""
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
        """`peak_rss.py` spells a gigabyte `1e9` and the reproduction path above is written as a floor of four of them, so the natural spelling of every byte-count keyword is a float. A float reaching a width fails far from the call that caused it — `range` raises on it and an argv carries it as `-n 4.0` — so each one is truncated on the way in."""
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
        """The cap is a count rather than a byte figure, so the annotation stays `int` and pyright refuses a float at any call site in this tree. The coercion is for the harnesses the module docstring names, which import it unchecked."""
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
        """Both parameters really move the answer, which is what lets an earlier policy's widths be reproduced without today's constants being fitted to them."""
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
        """The sample container's leaf scope states 4 GB and an ancestor states 2, so a reader that stopped at the leaf would answer the looser figure and the container would be OOM-killed at the tighter one."""
        assert memory_budget._cgroup_memory_limit_bytes(SAMPLES / "container-v2") == 2_000_000_000

    def test_a_v1_unlimited_sentinel_is_absent_and_the_containers_own_limit_binds(self):
        """`memory.limit_in_bytes` spells unlimited as a page-rounded 2**63-1, which reads back as a perfectly good int and would clamp nothing while looking as though it had."""
        assert memory_budget._cgroup_memory_limit_bytes(SAMPLES / "container-v1") == 2_147_483_648

    def test_memory_high_is_a_limit_too_and_not_only_memory_max(self, tmp_path: Path):
        """The checked-in v2 chain carries a `memory.high`, but its tightest limit is a `memory.max`, so only a root whose sole limit is a high shows that both v2 names are read."""
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
        """Which is what makes both clamps free on Darwin: one failed open apiece and no walk at all."""
        assert memory_budget._cgroup_memory_limit_bytes(SAMPLES / "no-such-box") is None
        assert memory_budget._cgroup_cpu_allowance(SAMPLES / "no-such-box") is None

    def test_the_cpu_quota_clamp_reads_v2_and_v1_alike(self):
        """v2's leaf states two cores under an ancestor's `max 100000`, and v1's container states a core and a half under a mount root whose quota is -1; both answer two whole cores."""
        assert memory_budget._cgroup_cpu_allowance(SAMPLES / "container-v2") == 2
        assert memory_budget._cgroup_cpu_allowance(SAMPLES / "container-v1") == 2

    def test_usable_cores_takes_the_cgroup_quota_when_one_is_stated(self):
        """The CPU clamp is a separate step from the memory one because it answers a separate question: `os.process_cpu_count` reads the affinity mask on Linux but not the CFS quota, so a quota-limited container that was never pinned reports every core the host has."""
        host = os.process_cpu_count() or os.cpu_count() or 1
        assert memory_budget.usable_cores(SAMPLES / "container-v2") == min(host, 2)
        assert memory_budget.usable_cores(SAMPLES / "container-v1") == min(host, 2)
        assert memory_budget.usable_cores(SAMPLES / "host-unlimited") == memory_budget.usable_cores(
            SAMPLES / "no-such-box"
        )

    def test_the_memory_clamp_is_linux_only(self):
        """`sysconf` reads the host inside a container, so the clamp is the entire correctness story there — and it is gated on the platform, so a Darwin box pointed at the same sample tree still answers its own memory."""
        assert (
            memory_budget.total_memory_bytes(platform="linux", cgroup_root=SAMPLES / "container-v2")
            == 2_000_000_000
        )
        assert memory_budget.total_memory_bytes(
            platform="darwin", cgroup_root=SAMPLES / "container-v2"
        ) == memory_budget.total_memory_bytes(platform="darwin")

    def test_meminfo_is_the_linux_fallback_where_sysconf_cannot_answer(self, monkeypatch: pytest.MonkeyPatch):
        """A Linux box whose `os.sysconf_names` has no `SC_PHYS_PAGES` falls through to `/proc/meminfo`, then to the last resort — which equals the shipped reserve floor, so an unprobeable box leaves no budget and every width falls to one rather than to a guess. Darwin never takes the meminfo arm at all."""
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
        """Which is what lets a container state its own allowance, a large box reproduce a small box's widths, and a dry run print the same plan on every machine."""
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", "12345678901")
        assert memory_budget.total_memory_bytes() == 12_345_678_901
        assert (
            memory_budget.total_memory_bytes(platform="linux", cgroup_root=SAMPLES / "container-v2")
            == 12_345_678_901
        )

    def test_it_moves_the_box_and_never_the_policy(self, monkeypatch: pytest.MonkeyPatch):
        """It is a probe override, not a policy one: the reserve applied on top is the same reserve, and the floor and fraction parameters still decide the width above it."""
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
        """A typo in a reproduction knob must leave the probe in charge rather than take a build down, so only a bare decimal count of bytes is read and everything else falls through."""
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
        """Which is the whole reason it exists: a reader surprised by a width audits its derivation instead of trusting it."""
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
    """The hazard issue #85 names: the tempting next move is a central `UNIT_COSTS` mapping, which would hold the numbers while leaving their arguments behind at the call sites that have to justify them. Pinning the public surface is what makes that move loud instead of quiet."""
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
