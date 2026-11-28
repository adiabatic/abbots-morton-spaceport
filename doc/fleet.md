# The build fleet

Nothing in the build reads this file. Every width is derived at run time from the machine the build runs on: `engine/tools/memory_budget.py` measures the machine and does the arithmetic, and each per-unit cost is defined at its call site. This file is the key for reading output tagged with a host, such as `make cycle-timings` rows, the "width here" lines from `make job-costs`, and prose that mentions "the 18-core machine" or "the 48 GiB box". Hostnames are not recorded in the repository. Match a timings row to its machine by the `cpus=` and `ram=` values the row prints.

| Machine             | Chip          | CPU cores                         | RAM                         |
| ------------------- | ------------- | --------------------------------- | --------------------------- |
| MacBook Pro         | M5 Pro        | 18 — 6 super + 12 performance     | 48 GiB (prints as 51.54 GB) |
| Mac mini (2024)     | M4 Pro        | 12 — 4 efficiency + 8 performance | 48 GiB (prints as 51.54 GB) |
| HP OmniDesk desktop | Ryzen 7 8700G | 16 — 8 cores, two threads each    | 32 GiB (prints as 32.70 GB) |

The 10-core M1 Pro 32 GiB MacBook Pro (2021) that some measurements cite, whose rows print `cpus=10` and `ram=34.36GB`, is not in the fleet.

- The two Macs run macOS on Apple silicon, and the HP desktop runs Debian on x86-64. § What is Mac-only lists what the HP desktop goes without.
- The tools print decimal gigabytes, so 48 GiB prints as 51.54 GB. The HP desktop's integrated graphics hold part of its 32 GiB, so it prints 32.70 GB where the M1 Pro's 32 GiB printed 34.36 GB. The fleet tests (`engine/test_memory_budget.py`, `engine/test_artifact_cycle.py`, and others) define the fleet's RAM size in bytes as `MACHINE_48_GIB`, so their width assertions, such as whether the whole kernel delta wave fits and the corpus build's worker count, use the real machines' memory.
- The M5 Pro row uses Apple's [super/performance terminology](https://www.apple.com/newsroom/2026/03/apple-debuts-m5-pro-and-m5-max-to-supercharge-the-most-demanding-pro-workflows/): super cores are the fastest tier, and performance cores use a separate design optimized for efficient multithreaded work.
- `memory_budget.usable_cores()` counts every available core, and no budget weights core classes, so a width of N may run on a mix of core classes. On the HP desktop it counts the sixteen hardware threads, two to a core.
- This file does not record the widths each machine gets. Run `make job-costs` (its "width here" lines) or `make cycle-timings` on the machine to see them for the constants as they currently stand. The deep sweep's width follows the window count at the maximum length it is asked for, up to its window ceiling, so `make conform-deep` states its own on its first line, with the arithmetic, and `engine/test_deep_sweep.py` checks what every machine gets at the length-5 and length-6 unit bounds it states.

## What is Mac-only

The build, the gates and the cycle run on Linux: `make all`, `make test`, `make test-engine` and `make artifact-cycle` pass on the HP desktop, at widths derived from its own memory and cores, and the six fonts it builds are the Macs' fonts byte for byte. `HACKING.md` names what a Linux machine needs installed. The tools below read macOS interfaces, and a Linux run does the following without them.

- **The write meter** (`engine/tools/write_meter.py`) reads a process tree's writes from its resource coalition, which only Darwin has. On Linux every reading records that it has none, so a cycle pass prints `[warn]` lines saying so, its summary gives the pass's writes as unread, and its run line records the `device` basis with no figure, a line `make cycle-timings ARGS='--writes'` counts and leaves out.
- **The launcher** (`engine/tools/job_launch.py`) starts a launchd job. On Linux it exits 125 saying a launchd job needs macOS, so `make fault-kit` and `make memory-series` do not run there.
- **The fault kit** (`engine/tools/fault_kit.py`) refuses a run whose first write-meter reading is not in the own basis, which no Linux reading is.
- **The memory series** (`engine/tools/memory_series.py`) runs on Linux when started directly, as `.venv/bin/python -m engine.tools.memory_series`, since the launcher behind `make memory-series` does not. Its line there reads the compressor's occupancy, the swap-out count and the device's write counter as null, which come from `vm_stat` and `ioreg`, its writes in the `device` basis with no figure, and no footprint for a reaped walk.
- **A process's footprint** (`engine/tools/peak_rss.py`) is, on Linux, its resident set plus its swapped pages, and its peak footprint is its resident peak, which under-reads a process that swapped. A reaped child's peak footprint is not read there, so the table build's `[t] kernel_build_tables` line carries its resident peak alone and the kernel-build row of `make job-costs` reads that.
- **The kernel's allocator setting** (`kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT`) names a macOS allocator switch and is set only there. Every kernel constant is measured on a Mac under it; a Linux machine divides its memory by the same constants, and `make job-costs` compares what it measured against them.
- **The power mode** a run line records comes from `pmset` on a Mac and from `powerprofilesctl get` on Linux (`cycle_timings.power_mode`), and reads as unread on a Linux machine without power-profiles-daemon.
- **`make test-slowly`** confines the suite with `taskpolicy` and sizes it from `sysctl hw.perflevel*`, and says so and exits on a machine without them.
- **The CoreText smoke check** (`engine/pipeline/coretext_smoke.py`) compares HarfBuzz with CoreText through a Swift helper, so it is a Mac's check by nature. No gate runs it.
- **A tree clone** (`landing.clone_tree`) is an APFS copy-on-write clone on a Mac and a plain copy elsewhere, which writes the tree's bytes again.
- **The detach recipe** in `doc/running-long-steps.md` holds the Mac awake with `caffeinate`; that file gives the Linux form.

The Macs' Python packs with the system's zlib and the HP desktop's with zlib-ng, which packs the same bytes differently. The accepted snapshot's digest rests on each file's unpacked content for that reason (`engine/review/snapshot.py`), so either platform remakes a snapshot the other accepted.
