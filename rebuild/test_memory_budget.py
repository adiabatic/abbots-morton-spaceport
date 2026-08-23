"""The memory-budget policy's own tests, and the reproduction of the two widths already on record (issue #63, sub-issue #85). Almost everything here is a pure function over an invented box, because `total_bytes`, `floor_bytes` and `fraction` are keywords on every policy function rather than module lookups; only the handful of live-probe tests touch the host, and those assert properties — positive, plausible, at least one core — with the single exception the issue permits, `total_memory_bytes()` against `sysctl -n hw.memsize` behind a Darwin guard. The probes are exercised the way the module split them to be exercised: pure parsers over the checked-in text under `rebuild/fixtures/memory_budget/`, and the two cgroup readers pointed at a sample filesystem root, so every container case is proven on a laptop. The three measured constants below come from the record rather than from solving for an answer — `KERNEL_CONFIG_BYTES` is AGENTS.md's "roughly nine gigabytes" for one kernel configuration in flight, corroborated by `build_tables_total` in the cycle timings journal; `FONT_POOL_BYTES` is ten font-suite workers at the 0.11-0.28 GB apiece the root conftest's `pytest_xdist_auto_num_workers` records; and `ISSUE_RESERVE_FLOOR_BYTES` is the 4 GB floor issue #85 stated its two facts under, passed explicitly because the shipped floor is now 8 GB. That the formula reproduces both facts under the issue's floor and lands on the shipped `KERNEL_THREADS_DEFAULT` under its own is the whole claim: the policy is shown reproducing measurements taken independently of it, not fitted to them. Nothing here reads a live build artifact, so the whole module is contracts-lane — the audit guard in `rebuild/conftest.py` is what keeps it there, by failing any contracts item that reads `rebuild/out/`, `tmp/`, or a root `verdicts-*` store."""

import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from rebuild.pipeline.kernel_exec import KERNEL_THREADS_DEFAULT
from rebuild.tools import memory_budget

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES = REPO_ROOT / "rebuild" / "fixtures" / "memory_budget"

KERNEL_CONFIG_BYTES = 9_000_000_000
FONT_POOL_BYTES = 2_800_000_000
ISSUE_RESERVE_FLOOR_BYTES = 4_000_000_000
BOX_32_GIB = 34_359_738_368
BOX_32_GB = 32_000_000_000
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

shipped_kernel_default = pytest.mark.skipif(
    "AMS_KERNEL_THREADS" in os.environ,
    reason="AMS_KERNEL_THREADS has moved KERNEL_THREADS_DEFAULT off the shipped width this reproduces",
)


@pytest.fixture(autouse=True)
def _no_inherited_override(monkeypatch: pytest.MonkeyPatch):
    """Every probe assertion here is about the box, not about whatever the shell that started pytest had to say on the subject, so the one environment variable this module reads is cleared before each test and set back only by the tests whose subject it is."""
    monkeypatch.delenv("AMS_TOTAL_MEMORY_BYTES", raising=False)


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

    @shipped_kernel_default
    @pytest.mark.parametrize("total", SPELLINGS_OF_32_GB)
    def test_subtracting_the_font_pool_lands_on_the_shipped_kernel_default(self, total: int):
        """The second recorded fact: `KERNEL_THREADS_DEFAULT` ships one below #46's solo 3 because a cycle runs the fan-out beside a pytest pool. Say that out loud — subtract the font suite's ten co-resident workers — and the same formula answers the shipped constant rather than the solo width, which is the argument the kernel's docstring makes in prose."""
        assert (
            memory_budget.how_many_fit(
                KERNEL_CONFIG_BYTES,
                coresident_bytes=FONT_POOL_BYTES,
                total_bytes=total,
                floor_bytes=ISSUE_RESERVE_FLOOR_BYTES,
            )
            == KERNEL_THREADS_DEFAULT
            == 2
        )

    @shipped_kernel_default
    @pytest.mark.parametrize("total", SPELLINGS_OF_32_GB)
    def test_the_shipped_eight_gigabyte_floor_yields_the_kernel_default_on_its_own(self, total: int):
        """The policy this repo actually ships reserves 8 GB rather than the issue's 4, which costs the same 32 GB box a whole configuration: it answers the shipped default with nothing subtracted, and still answers it with the font pool subtracted. So the shipped floor does not contradict `KERNEL_THREADS_DEFAULT` — it only declines to reproduce #46's solo 3, which is why the floor is a parameter and the reproduction above can still pass the issue's."""
        assert memory_budget.how_many_fit(KERNEL_CONFIG_BYTES, total_bytes=total) == KERNEL_THREADS_DEFAULT
        assert (
            memory_budget.how_many_fit(
                KERNEL_CONFIG_BYTES, coresident_bytes=FONT_POOL_BYTES, total_bytes=total
            )
            == KERNEL_THREADS_DEFAULT
        )


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
