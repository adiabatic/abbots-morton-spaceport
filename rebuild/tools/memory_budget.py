"""How many of a thing fit in the box, the counterpart to `peak_rss.py`'s how much one of them costs (issue #63, sub-issue #85). The pairing is the argument for a module apiece rather than a probe per call site: #51 found eight independent `ru_maxrss` conversions and three mutually incompatible `*_gb` units before it collapsed them into one yardstick, and a memory probe grown site by site would end the same way. Everything here answers in bytes, and the presentation unit is `peak_rss`'s rather than a second one — `format_gb` is imported instead of spelled again, so a figure this module prints and a figure a `[t]` line carries are the same decimal gigabyte (1 GB = 1e9 bytes).

The policy it owns: read total physical memory clamped by any cgroup limit, never free or available memory, subtract an explicit reserve, integer-divide by a measured per-unit peak, `min()` against an optional non-memory cap, and floor at one. `how_many_fit` is that arithmetic and `describe_fit` is the same arithmetic said out loud — one clause naming the per-unit cost, the box, the reserve and whatever was subtracted for a co-resident pool, so a reader surprised by a width can audit its derivation instead of trusting it. The cap is a bound that has nothing to do with memory (a core count from `usable_cores`, a data count like the acceptance-configuration set) and it applies before the floor, so a cap of zero still answers one; the floor at one is not optional, because a build that refuses to start on a small machine is strictly worse than one that runs slowly. The reserve is `max(RESERVE_FLOOR_BYTES, RESERVE_FRACTION * total)`, floor and fraction both, because they model different risks: the OS-and-desktop floor is roughly constant across hardware, while the fraction exists because a bigger box is one someone keeps more open on, and pessimism should scale with the number it protects. Both ship as module constants and both are keyword parameters, so a test can reproduce a width recorded under an earlier policy without the shipped policy being fitted to it.

Free and available memory are never read, which is the surprising half of the policy, so the reasons in one place: `vm.swapusage` is sticky and lagging, so a healthy idle box reads gigabytes used and a swap tripwire would veto every run forever; macOS drives free pages toward zero by design, so `vm_stat`'s free count is wrong as headroom by an order of magnitude; `SC_AVPHYS_PAGES` is absent from `os.sysconf_names` on Darwin entirely and is `MemFree` rather than `MemAvailable` on Linux; and Darwin's `host_statistics64` availability is an opinion rather than a number, with two defensible reconstructions of one sample disagreeing by gigabytes. Underneath all four, any current-availability reading is irreproducible and racy — it moves when a browser opens, and something allocates between the read and the fork — so total less a stated reserve is both the more honest figure and the only one two runs on the same box agree on. Issue #63 carries the checked table this compresses.

The probes are split so none of that needs a host to test: each is an I/O shim that returns text beside a pure parser that takes it, `_cgroup_memory_limit_bytes` and `_cgroup_cpu_allowance` take the filesystem root to read under so a test points them at `rebuild/fixtures/memory_budget/` instead of requiring a container, and `total_bytes` is a keyword on every policy function rather than a module lookup so every policy assertion is a pure function over an invented box. `os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")` is the whole portable probe — byte-identical to `sysctl -n hw.memsize` on Darwin, correct on Linux, no subprocess and no dependency — with `/proc/meminfo`'s `MemTotal` as the Linux fallback where `SC_PHYS_PAGES` is missing from `os.sysconf_names`, and `_LAST_RESORT_TOTAL_BYTES` where neither answers at all. On Linux that figure is clamped by the least limit any cgroup from `/proc/self/cgroup` to the root imposes, reading the literal `max` and the v1 unlimited sentinels as absent rather than as an `int`; the clamp is the entire correctness story in a container, because `sysconf` reads the host there, and it is the step Bazel's `HOST_RAM` omits and the reason Bazel gets OOM-killed in containers.

`AMS_TOTAL_MEMORY_BYTES` is the one environment variable here, and it overrides the probe and never the policy: a container states its own allowance, a large box reproduces a small box's widths, and a `--dry-run` prints the same plan on every machine. Junk in it is ignored rather than raised on, because a typo in a reproduction knob must not take a build down. `AMS_KERNEL_THREADS` and `PYTEST_XDIST_AUTO_NUM_WORKERS` keep winning unconditionally over every width in the tree; this module neither reads nor undercuts them, and a call site that honors one honors it before it asks anything here.

No per-unit cost lives here, and the omission is the point. The repo has exactly two genuinely memory-bound fan-outs, and each argues its width from facts that are not fungible — a live configuration holding its whole working set until it has written its artifacts for the kernel fan-out, a parent holding the whole corpus beside workers holding slices of it for the review-surface build, where what is divided is the box less that parent — so a central `UNIT_COSTS` mapping would hold the numbers while leaving their arguments behind at the call sites. Costs stay at their call sites as named constants, in the docstring that already has to justify the width, and this module owns only the arithmetic. `CONFIG_PEAK_BYTES` in `rebuild/pipeline/kernel_exec.py` is the worked example and the shape a second call site should copy: the constant, one sentence naming what measured it and where that measurement is still reported, and a single `how_many_fit` resolved right where the width is named, so the argument for a width and the width itself are never in different files. The review-surface build is the other, in `rebuild/tools/artifact_cycle.py` and in the same shape with one term more: `SURFACE_WORKER_BYTES` is what one worker holds and is the divisor, `SURFACE_PARENT_BYTES` is the parent that holds the whole corpus at any width and so is subtracted from the box before the division rather than smeared through it, and `SURFACE_JOBS_CAP` is the non-memory bound — the one call site here whose flat half is as large as its divided half, which is why it names two constants rather than one. The root `conftest.py` divides by nothing: no rebuild-suite worker reads a live build artifact, so every `-n auto` it answers takes the cores, and there is no per-worker cost for it to restate.

Stdlib-only on purpose, exactly as `peak_rss.py` is: the bench harnesses import these under alternative interpreters and from trees where only the repo root is on `sys.path`, and no width should be undecidable for want of `psutil`.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from rebuild.tools.peak_rss import format_gb

RESERVE_FLOOR_BYTES = 8_000_000_000
RESERVE_FRACTION = 0.15

_TOTAL_MEMORY_ENV = "AMS_TOTAL_MEMORY_BYTES"
_LAST_RESORT_TOTAL_BYTES = 8_000_000_000
_IMPLAUSIBLE_LIMIT_BYTES = 1 << 62
_V2_MEMORY_FILES = ("memory.max", "memory.high")
_V1_CPU_MOUNTS = ("cpu", "cpu,cpuacct")
_MEMINFO_TOTAL = re.compile(r"^MemTotal:\s*(\d+)\s*kB\s*$", re.IGNORECASE | re.MULTILINE)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError, ValueError:
        return None


def _parse_meminfo_total_bytes(text: str) -> int | None:
    """The physical memory `/proc/meminfo`'s `MemTotal` line states, in bytes, or None where the text carries no such line. The file's `kB` suffix means KiB, so the figure is multiplied by 1024 and not by 1000."""
    match = _MEMINFO_TOTAL.search(text)
    return int(match.group(1)) * 1024 if match else None


def _parse_memory_limit(text: str) -> int | None:
    """The byte limit a cgroup memory file states, or None where it states none — an empty or unparsable file, the literal `max` that v2 writes for an unconstrained cgroup, a non-positive value, or a v1 sentinel: `memory.limit_in_bytes` spells unlimited as a page-rounded `2**63-1` (typically 9223372036854771712), and reading that as an `int` would clamp nothing while looking as though it had."""
    token = text.strip()
    if not token or token == "max":
        return None
    try:
        value = int(token)
    except ValueError:
        return None
    return value if 0 < value < _IMPLAUSIBLE_LIMIT_BYTES else None


def _parse_cpu_max(text: str) -> int | None:
    """The whole cores cgroup v2's `cpu.max` allows — its two fields are a quota and a period in microseconds — or None where the quota field is the literal `max` or the pair is unparsable. A fractional allowance rounds up, because a quota of one and a half cores is still two processes' worth of runnable work and the scheduler, not this module, is what throttles them."""
    fields = text.split()
    if len(fields) < 2 or fields[0] == "max":
        return None
    try:
        quota = int(fields[0])
        period = int(fields[1])
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return -(-quota // period)


def _parse_cpu_cfs_quota(quota_text: str, period_text: str) -> int | None:
    """The whole cores cgroup v1's `cpu.cfs_quota_us` and `cpu.cfs_period_us` allow between them, rounded up the way `_parse_cpu_max` rounds, or None where there is no quota — v1 spells unlimited as a quota of -1."""
    try:
        quota = int(quota_text.strip())
        period = int(period_text.strip())
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return -(-quota // period)


def _parse_proc_cgroup(text: str) -> dict[str, str]:
    """The controller-to-path mapping `/proc/self/cgroup` states: the unified v2 line (`0::`, no controller list) lands under the empty-string key, and every v1 line contributes one entry per controller it names, so a caller asks for `memory` or `cpu` by name and a v2-only box answers only under the empty key. The first line naming a controller wins. Paths are as the file writes them, absolute-looking but relative to whichever mount holds that hierarchy."""
    paths: dict[str, str] = {}
    for line in text.splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3:
            continue
        _, controllers, path = fields
        if not controllers:
            paths.setdefault("", path)
            continue
        for controller in controllers.split(","):
            paths.setdefault(controller, path)
    return paths


def _cgroup_dirs(mount: Path, relative: str) -> list[Path]:
    """Every directory from `mount / relative` up to `mount` itself, leaf first, because a limit can be imposed anywhere along that chain and the tightest one binds."""
    dirs = [mount]
    for part in relative.split("/"):
        if part:
            dirs.append(dirs[-1] / part)
    return list(reversed(dirs))


def _cgroup_memory_limit_bytes(root: str | Path = "/") -> int | None:
    """The least memory limit any cgroup on the path from `/proc/self/cgroup` to the root imposes, or None where none of them imposes one. `root` is the filesystem root to read under, so a test points this at a sample tree under `rebuild/fixtures/memory_budget/` rather than requiring a container: `<root>/proc/self/cgroup` names the hierarchies, `<root>/sys/fs/cgroup/<path>/memory.max` and `memory.high` are the v2 files, and `<root>/sys/fs/cgroup/memory/<path>/memory.limit_in_bytes` is the v1 one. A root with no readable `/proc/self/cgroup` answers None at the first open, which is what makes this free on Darwin."""
    base = Path(root)
    text = _read_text(base / "proc" / "self" / "cgroup")
    if text is None:
        return None
    controllers = _parse_proc_cgroup(text)
    mount = base / "sys" / "fs" / "cgroup"
    candidates: list[Path] = []
    if "" in controllers:
        for directory in _cgroup_dirs(mount, controllers[""]):
            candidates.extend(directory / name for name in _V2_MEMORY_FILES)
    if "memory" in controllers:
        for directory in _cgroup_dirs(mount / "memory", controllers["memory"]):
            candidates.append(directory / "memory.limit_in_bytes")
    limits: list[int] = []
    for path in candidates:
        stated = _read_text(path)
        limit = _parse_memory_limit(stated) if stated is not None else None
        if limit is not None:
            limits.append(limit)
    return min(limits) if limits else None


def _cgroup_cpu_allowance(root: str | Path = "/") -> int | None:
    """The least whole-core allowance any cgroup CPU quota on the path from `/proc/self/cgroup` to the root imposes, or None where none of them imposes one. Same `root` contract as `_cgroup_memory_limit_bytes`, over `cpu.max` for v2 and the `cpu.cfs_quota_us` / `cpu.cfs_period_us` pair under either v1 mount spelling (`cpu` or `cpu,cpuacct`) for v1. This is a separate step from the memory clamp because it answers a separate question: `os.process_cpu_count` already reads the affinity mask on Linux but not the CFS quota, so a quota-limited container that was never pinned reports every core the host has."""
    base = Path(root)
    text = _read_text(base / "proc" / "self" / "cgroup")
    if text is None:
        return None
    controllers = _parse_proc_cgroup(text)
    mount = base / "sys" / "fs" / "cgroup"
    allowances: list[int] = []
    if "" in controllers:
        for directory in _cgroup_dirs(mount, controllers[""]):
            stated = _read_text(directory / "cpu.max")
            allowance = _parse_cpu_max(stated) if stated is not None else None
            if allowance is not None:
                allowances.append(allowance)
    if "cpu" in controllers:
        for name in _V1_CPU_MOUNTS:
            for directory in _cgroup_dirs(mount / name, controllers["cpu"]):
                quota = _read_text(directory / "cpu.cfs_quota_us")
                period = _read_text(directory / "cpu.cfs_period_us")
                allowance = (
                    _parse_cpu_cfs_quota(quota, period) if quota is not None and period is not None else None
                )
                if allowance is not None:
                    allowances.append(allowance)
    return min(allowances) if allowances else None


def _sysconf_total_bytes() -> int | None:
    """The portable total-memory probe, or None where the platform has no `SC_PHYS_PAGES` in `os.sysconf_names` or declines to answer one of the two names."""
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
    except ValueError, OSError:
        return None
    return page_size * page_count if page_size > 0 and page_count > 0 else None


def _env_total_bytes() -> int | None:
    """The `AMS_TOTAL_MEMORY_BYTES` override in whole bytes, or None where it is unset, empty, not an integer, or not positive. Only a bare decimal count of bytes is read — no `GB` suffix, no exponent — and anything else is ignored rather than raised on, so a typo in a reproduction knob leaves the probe in charge instead of taking a build down."""
    raw = os.environ.get(_TOTAL_MEMORY_ENV, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def total_memory_bytes(platform: str = sys.platform, cgroup_root: str | Path = "/") -> int:
    """The memory this box is willing to lend, in bytes: `AMS_TOTAL_MEMORY_BYTES` if it states one, else `SC_PAGE_SIZE * SC_PHYS_PAGES`, else `/proc/meminfo`'s `MemTotal` on Linux, else `_LAST_RESORT_TOTAL_BYTES` — which equals the shipped reserve floor, so a box that answered neither probe leaves no budget at all and every width falls to one rather than to a guess. On Linux the probed figure is then clamped by `_cgroup_memory_limit_bytes`. Both keywords are injection points rather than lookups: a test on Darwin drives the whole Linux path by passing `platform="linux"` beside a `cgroup_root` pointing at a sample tree."""
    override = _env_total_bytes()
    if override is not None:
        return override
    linux = platform.startswith("linux")
    total = _sysconf_total_bytes()
    if total is None and linux:
        stated = _read_text(Path(cgroup_root) / "proc" / "meminfo")
        total = _parse_meminfo_total_bytes(stated) if stated is not None else None
    if total is None:
        total = _LAST_RESORT_TOTAL_BYTES
    if linux:
        limit = _cgroup_memory_limit_bytes(cgroup_root)
        if limit is not None:
            total = min(total, limit)
    return total


def usable_cores(cgroup_root: str | Path = "/") -> int:
    """The cores this process may actually run on, never below one: `os.process_cpu_count()` (the affinity mask on Linux) or `os.cpu_count()`, clamped by `_cgroup_cpu_allowance` as a separate step. There is no platform keyword here, unlike `total_memory_bytes`, because the clamp needs no gate — a box with no readable `/proc/self/cgroup` answers None at the first open, and on Darwin `os.process_cpu_count is os.cpu_count`, so the whole clamp costs one failed `open` there."""
    cores = os.process_cpu_count() or os.cpu_count() or 1
    allowance = _cgroup_cpu_allowance(cgroup_root)
    if allowance is not None:
        cores = min(cores, allowance)
    return max(1, cores)


def os_reserve_bytes(
    *,
    total_bytes: int | None = None,
    floor_bytes: float = RESERVE_FLOOR_BYTES,
    fraction: float = RESERVE_FRACTION,
) -> int:
    """What a box keeps for the operating system, the desktop and whatever else the person at the keyboard has open — `max(floor_bytes, fraction * total_bytes)`, truncated to whole bytes. `total_bytes` defaults to the probe and is a keyword so a policy assertion can be a pure function over an invented box; `floor_bytes` and `fraction` are keywords so an earlier policy's widths stay reproducible without today's constants being fitted to them. Every byte count is truncated on the way in, so a floor written the way this repo writes a gigabyte (`floor_bytes=4e9`) answers a whole number of bytes rather than carrying a float out through a width."""
    total = total_memory_bytes() if total_bytes is None else int(total_bytes)
    return max(int(floor_bytes), int(total * fraction))


def _raw_fit(per_unit: int, budget: int, cap: int | None) -> int:
    """The width before the floor at one, so `describe_fit` can tell whether the floor is what decided it. An unmeasured unit — a per-unit cost of zero or less — never gets a memory-derived width (issue #63's rule), so it answers the cap alone, or one where there is no cap, rather than raising `ZeroDivisionError` from inside the formula."""
    fits = budget // per_unit if per_unit > 0 else (cap if cap is not None else 1)
    return min(fits, cap) if cap is not None else fits


def how_many_fit(
    per_unit_bytes: float,
    *,
    coresident_bytes: float = 0,
    cap: int | None = None,
    total_bytes: int | None = None,
    floor_bytes: float = RESERVE_FLOOR_BYTES,
    fraction: float = RESERVE_FRACTION,
) -> int:
    """How many units of `per_unit_bytes` fit: the box less its reserve less `coresident_bytes`, integer-divided by the per-unit cost, `min()`'d against `cap`, floored at one. Every byte count is truncated to a whole number before it enters the arithmetic, and the division floors, so the answer counts only units that fit whole. The floor is applied after the cap and therefore wins over a cap of zero or less — the guarantee that this never hands a pool a width it cannot start with is worth more than agreeing with a caller who had nothing to run and asked anyway. A `per_unit_bytes` of zero or less means the unit is unmeasured and answers `cap` (or one), because inventing a divisor would look like evidence. A `coresident_bytes` below zero subtracts nothing rather than adding to the budget: a call site that reaches one by computing a pool's footprint as a difference is the one input that could otherwise err high, and every other degenerate input here already fails toward a narrower width."""
    total = total_memory_bytes() if total_bytes is None else int(total_bytes)
    reserve = os_reserve_bytes(total_bytes=total, floor_bytes=floor_bytes, fraction=fraction)
    budget = total - reserve - max(0, int(coresident_bytes))
    return max(1, _raw_fit(int(per_unit_bytes), budget, None if cap is None else int(cap)))


def describe_fit(
    per_unit_bytes: float,
    *,
    coresident_bytes: float = 0,
    cap: int | None = None,
    total_bytes: int | None = None,
    floor_bytes: float = RESERVE_FLOOR_BYTES,
    fraction: float = RESERVE_FRACTION,
) -> str:
    """The one clause `how_many_fit`'s answer drops into a plan line or a docstring, so a reader surprised by a width can audit its derivation instead of trusting it. Comma-joined, no trailing period, and every optional clause is present only when it applies: `2 at 9.00 GB each out of 34.36 GB total`, then `less a reserve of 8.00 GB`, then `less 2.80 GB co-resident` when anything was subtracted for a co-resident pool, then `capped at 8` when a cap was given, then `floored at one` when the arithmetic answered below one. An unmeasured unit says so instead of the first two clauses — `1 at an unmeasured per-unit cost, so no memory-derived width` — and still carries the cap and floor clauses."""
    total = total_memory_bytes() if total_bytes is None else int(total_bytes)
    reserve = os_reserve_bytes(total_bytes=total, floor_bytes=floor_bytes, fraction=fraction)
    per_unit = int(per_unit_bytes)
    coresident = max(0, int(coresident_bytes))
    limit = None if cap is None else int(cap)
    raw = _raw_fit(per_unit, total - reserve - coresident, limit)
    count = max(1, raw)
    if per_unit <= 0:
        clauses = [f"{count} at an unmeasured per-unit cost", "so no memory-derived width"]
    else:
        clauses = [
            f"{count} at {format_gb(per_unit)} GB each out of {format_gb(total)} GB total",
            f"less a reserve of {format_gb(reserve)} GB",
        ]
        if coresident > 0:
            clauses.append(f"less {format_gb(coresident)} GB co-resident")
    if limit is not None:
        clauses.append(f"capped at {limit}")
    if raw < 1:
        clauses.append("floored at one")
    return ", ".join(clauses)
