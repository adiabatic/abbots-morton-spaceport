"""Compute how many units of work fit in the machine's memory, the counterpart to `peak_rss.py`, which measures what one unit costs. All figures are in bytes, and printed figures use `peak_rss.format_gb` (decimal gigabytes, 1 GB = 1e9 bytes), so a figure printed here and one on a `[t]` line use the same unit.

The policy: read total physical memory, clamped by any cgroup limit, never free or available memory; subtract a reserve; integer-divide by a measured per-unit peak; take the `min()` with an optional non-memory cap; and floor at one. `how_many_fit` does the arithmetic, and `describe_fit` states it as one clause (per-unit cost, total, reserve, anything subtracted for a co-resident pool) so a reader can check where a width came from. The cap is a bound unrelated to memory, such as a core count from `usable_cores` or the number of acceptance configurations. It applies before the floor, so a cap of zero still returns one. The floor at one is required: a build that refuses to start on a small machine is worse than one that runs slowly. The reserve is `max(RESERVE_FLOOR_BYTES, RESERVE_FRACTION * total)`. The floor covers the operating system and desktop, which cost roughly the same on any hardware; the fraction covers the larger workload people tend to keep open on a larger machine. Both are module constants and keyword parameters, so a test can reproduce a width recorded under an earlier policy without changing the shipped values.

Free and available memory are never read, for these reasons. `vm.swapusage` is sticky and lags, so a healthy idle machine shows gigabytes used and a swap check would block every run. macOS drives free pages toward zero, so `vm_stat`'s free count understates headroom by an order of magnitude. `SC_AVPHYS_PAGES` is missing from `os.sysconf_names` on Darwin and is `MemFree`, not `MemAvailable`, on Linux. Darwin's `host_statistics64` availability is an estimate, and two reasonable reconstructions of one sample disagree by gigabytes. Beyond those, any reading of current availability is irreproducible and racy: it changes when a browser opens, and something can allocate between the read and the fork. Total memory less a stated reserve is the figure two runs on the same machine agree on. Issue #63 has the table of these checks.

The probes are split so none of this needs a particular host to test. Each I/O shim returns text to a pure parser. `_cgroup_memory_limit_bytes` and `_cgroup_cpu_allowance` take the filesystem root to read under, so a test points them at `rebuild/fixtures/memory_budget/`. `total_bytes` is a keyword on every policy function, so every policy assertion is a pure function of an invented machine. The total-memory probe is `os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")`, which equals `sysctl -n hw.memsize` on Darwin and is correct on Linux, with no subprocess or dependency. On Linux without `SC_PHYS_PAGES` it falls back to `/proc/meminfo`'s `MemTotal`, and to `_LAST_RESORT_TOTAL_BYTES` when neither returns a value. On Linux the figure is then clamped by the lowest limit any cgroup imposes on the path from `/proc/self/cgroup` to the root, reading the literal `max` and the v1 unlimited values as no limit. Inside a container this clamp is what makes the figure correct, because `sysconf` reports the host's memory there.

`AMS_TOTAL_MEMORY_BYTES` is the only environment variable read here. It replaces the probe, never the policy, so a container can state its allowance, a large machine can reproduce a small machine's widths, and a `--dry-run` prints the same plan on every machine. An unparseable value is ignored, so a typo does not stop a build. `AMS_KERNEL_THREADS`, `AMS_REPLAY_THREADS`, `AMS_DEEP_REPLAY_THREADS` and `PYTEST_XDIST_AUTO_NUM_WORKERS` override the derived width of the table build's delta wave, the string replay, the deep replay and any `-n auto` pool respectively, and nothing derived narrows them, though a stated table-build or string-replay width is still capped at the configuration count and the cores. This module does not read them; each call site checks its override before calling here. The two pools sized by cores alone (`run_m1._core_bound_threads`: the window packing and the shipped-order walks) have no override, because what they hold is small and outside the table build's memory estimate.

No per-unit cost lives here. Each memory-bound fan-out argues its width from facts specific to it, so a central `UNIT_COSTS` mapping would separate the numbers from their arguments. Each cost is a named `*_BYTES` constant at its call site, whose comment or docstring records the measurements and justifies the width; `doc/parallelism.md` maps every width to its constant. The table build in `rebuild/pipeline/kernel_exec.py` shows the pattern: one constant per term, each with a comment naming what measured it and where that measurement is reported, and one `how_many_fit` call where the width is computed. Its two terms differ in kind. `DELTA_PEAK_BYTES` is what one delta holds through enumeration and its memo write, and is the divisor. `DEFAULT_MEMO_BYTES` is the snapshot `default` keeps alive for the whole wave at any width, so it is subtracted from the machine's memory before the division. The review-surface build in `rebuild/tools/artifact_cycle.py` has the same shape: `SURFACE_WORKER_BYTES` is the divisor, `SURFACE_PARENT_BYTES` is the parent that holds the whole corpus at any width and is subtracted first, and `SURFACE_JOBS_CAP` is the non-memory cap. The root `conftest.py` divides by nothing: its `-n auto` takes the usable cores, because a font-suite worker (`FONT_SUITE_WORKER_BYTES`) is small enough that the cores limit the pool before memory does.

The module has no third-party dependencies, like `peak_rss.py`, because the bench harnesses import it under other interpreters and from trees where only the repo root is on `sys.path`, and no width should depend on `psutil` being installed.
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
    """Return the bytes `/proc/meminfo`'s `MemTotal` line states, or None when there is no such line. The file's `kB` means KiB, so the figure is multiplied by 1024."""
    match = _MEMINFO_TOTAL.search(text)
    return int(match.group(1)) * 1024 if match else None


def _parse_memory_limit(text: str) -> int | None:
    """Return the byte limit a cgroup memory file states, or None when it states none: an empty or unparseable file, the literal `max` that v2 writes for an unconstrained cgroup, a non-positive value, or v1's unlimited value. v1's `memory.limit_in_bytes` writes unlimited as a page-rounded `2**63-1` (typically 9223372036854771712), which would otherwise read as a limit that clamps nothing."""
    token = text.strip()
    if not token or token == "max":
        return None
    try:
        value = int(token)
    except ValueError:
        return None
    return value if 0 < value < _IMPLAUSIBLE_LIMIT_BYTES else None


def _parse_cpu_max(text: str) -> int | None:
    """Return the whole cores cgroup v2's `cpu.max` allows (its fields are a quota and a period in microseconds), or None when the quota is the literal `max` or the pair does not parse. A fractional allowance rounds up: a quota of one and a half cores can still run two processes, and the scheduler throttles them."""
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
    """Return the whole cores cgroup v1's `cpu.cfs_quota_us` and `cpu.cfs_period_us` allow, rounded up as in `_parse_cpu_max`, or None when there is no quota (v1 writes unlimited as -1)."""
    try:
        quota = int(quota_text.strip())
        period = int(period_text.strip())
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return -(-quota // period)


def _parse_proc_cgroup(text: str) -> dict[str, str]:
    """Return the controller-to-path mapping `/proc/self/cgroup` states. The unified v2 line (`0::`, with no controller list) goes under the empty-string key, and each v1 line adds one entry per controller it names, so a v2-only machine has only the empty key. The first line naming a controller wins. Paths are as the file writes them: they look absolute but are relative to the mount that holds that hierarchy."""
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
    """Return every directory from `mount / relative` up to `mount`, leaf first, because a limit can be set anywhere along that chain and the lowest one applies."""
    dirs = [mount]
    for part in relative.split("/"):
        if part:
            dirs.append(dirs[-1] / part)
    return list(reversed(dirs))


def _cgroup_memory_limit_bytes(root: str | Path = "/") -> int | None:
    """Return the lowest memory limit any cgroup on the path from `/proc/self/cgroup` to the root sets, or None when none sets one. `root` is the filesystem root to read under, so a test can point it at a sample tree under `rebuild/fixtures/memory_budget/`: `<root>/proc/self/cgroup` names the hierarchies, `<root>/sys/fs/cgroup/<path>/memory.max` and `memory.high` are the v2 files, and `<root>/sys/fs/cgroup/memory/<path>/memory.limit_in_bytes` is the v1 file. Without a readable `/proc/self/cgroup`, as on Darwin, it returns None after one failed open."""
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
    """Return the lowest whole-core allowance any cgroup CPU quota on the path from `/proc/self/cgroup` to the root sets, or None when none sets one. `root` works as in `_cgroup_memory_limit_bytes`. It reads `cpu.max` for v2, and the `cpu.cfs_quota_us` and `cpu.cfs_period_us` pair under either v1 mount name (`cpu` or `cpu,cpuacct`). It is needed because `os.process_cpu_count` reads the affinity mask on Linux but not the CFS quota, so a quota-limited container that was never pinned reports every core the host has."""
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
    """Return `SC_PAGE_SIZE * SC_PHYS_PAGES`, or None when the platform lacks either name or returns no positive value."""
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
    except ValueError, OSError:
        return None
    return page_size * page_count if page_size > 0 and page_count > 0 else None


def _env_total_bytes() -> int | None:
    """Return the `AMS_TOTAL_MEMORY_BYTES` override in bytes, or None when it is unset, empty, not an integer, or not positive. Only a bare decimal count of bytes is accepted, with no `GB` suffix or exponent. Anything else is ignored, so a typo falls back to the probe instead of stopping a build."""
    raw = os.environ.get(_TOTAL_MEMORY_ENV, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def total_memory_bytes(platform: str = sys.platform, cgroup_root: str | Path = "/") -> int:
    """Return the machine's total memory in bytes: `AMS_TOTAL_MEMORY_BYTES` if set, else `SC_PAGE_SIZE * SC_PHYS_PAGES`, else `/proc/meminfo`'s `MemTotal` on Linux, else `_LAST_RESORT_TOTAL_BYTES`. The last equals the shipped reserve floor, so a machine where both probes fail has no budget and every width falls to one. On Linux the probed figure is then clamped by `_cgroup_memory_limit_bytes`. A test on Darwin exercises the Linux path by passing `platform="linux"` and a `cgroup_root` that points at a sample tree."""
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
    """Return the cores this process may run on, at least one: `os.process_cpu_count()` (which reads the affinity mask on Linux) or `os.cpu_count()`, clamped by `_cgroup_cpu_allowance`. Unlike `total_memory_bytes` it takes no platform keyword, because the clamp needs no platform check: without a readable `/proc/self/cgroup` it costs one failed `open`, and on Darwin `os.process_cpu_count is os.cpu_count`."""
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
    """Return the memory reserved for the operating system, the desktop and whatever else the user has open: `max(floor_bytes, fraction * total_bytes)`, truncated to whole bytes. `total_bytes` defaults to the probe. `floor_bytes` and `fraction` are keywords so a test can reproduce an earlier policy's widths. Byte counts are truncated on input, so a float such as `floor_bytes=4e9` still gives a whole number of bytes."""
    total = total_memory_bytes() if total_bytes is None else int(total_bytes)
    return max(int(floor_bytes), int(total * fraction))


def _raw_fit(per_unit: int, budget: int, cap: int | None) -> int:
    """Return the width before the floor at one, so `describe_fit` can tell whether the floor decided it. An unmeasured unit (a per-unit cost of zero or less) gets no memory-derived width: it returns the cap, or one when there is no cap, instead of dividing by zero."""
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
    """Return how many units of `per_unit_bytes` fit: total memory less the reserve less `coresident_bytes`, integer-divided by the per-unit cost, capped at `cap`, and floored at one. Byte counts are truncated to integers first, and the division rounds down, so only whole units count. The floor applies after the cap, so a cap of zero or less still returns one and a pool always gets a width it can start with. A `per_unit_bytes` of zero or less means the unit is unmeasured and returns `cap` (or one), since an invented divisor would look like a measurement. A negative `coresident_bytes` subtracts nothing: a call site that computes a pool's footprint as a difference could otherwise add to the budget, and every other degenerate input already narrows the width."""
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
    """Return `how_many_fit`'s arithmetic as one clause for a plan line, so a reader can check where a width came from. The parts are comma-joined with no trailing period, and each optional part appears only when it applies: `2 at 9.00 GB each out of 34.36 GB total`, then `less a reserve of 8.00 GB`, then `less 2.80 GB co-resident` when anything co-resident was subtracted, then `capped at 8` when a cap was given, then `floored at one` when the arithmetic came out below one. An unmeasured unit replaces the first two parts with `1 at an unmeasured per-unit cost, so no memory-derived width` and keeps the cap and floor parts."""
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
