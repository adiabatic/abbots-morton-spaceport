# The build fleet

Nothing in the build reads this file. Every width is derived at run time from the machine the build runs on: `rebuild/tools/memory_budget.py` measures the machine and does the arithmetic, and each per-unit cost is defined at its call site. This file is the key for reading output tagged with a host, such as `make cycle-timings` rows, the "width here" lines from `make job-costs`, and prose that mentions "the 18-core machine" or "the 48 GiB box". Hostnames are not recorded in the repository. Match a timings row to its machine by the `cpus=` and `ram=` values the row prints.

| Machine         | Chip   | CPU cores                         | RAM                         |
| --------------- | ------ | --------------------------------- | --------------------------- |
| MacBook Pro     | M5 Pro | 18 — 6 super + 12 performance     | 48 GiB (prints as 51.54 GB) |
| Mac mini (2024) | M4 Pro | 12 — 4 efficiency + 8 performance | 48 GiB (prints as 51.54 GB) |

The 10-core M1 Pro 32 GiB MacBook Pro (2021) that some measurements cite, whose rows print `cpus=10` and `ram=34.36GB`, is not in the fleet.

- The tools print decimal gigabytes, so 48 GiB prints as 51.54 GB. The fleet tests (`rebuild/test_memory_budget.py`, `rebuild/test_artifact_cycle.py`, and others) define the fleet's RAM size in bytes as `MACHINE_48_GIB`, so their width assertions, such as whether the whole kernel delta wave fits and the corpus build's worker count, use the real machines' memory.
- The M5 Pro row uses Apple's [super/performance terminology](https://www.apple.com/newsroom/2026/03/apple-debuts-m5-pro-and-m5-max-to-supercharge-the-most-demanding-pro-workflows/): super cores are the fastest tier, and performance cores use a separate design optimized for efficient multithreaded work.
- `memory_budget.usable_cores()` counts every available core, and no budget weights core classes, so a width of N may run on a mix of core classes.
- This file does not record the widths each machine gets. Run `make job-costs` (its "width here" lines) or `make cycle-timings` on the machine to see them for the constants as they currently stand. The deep sweep's width follows the window count at the maximum length it is asked for, up to its window ceiling, so `make conform-deep` states its own on its first line, with the arithmetic, and `rebuild/test_deep_sweep.py` checks what every machine gets at the length-5 and length-6 unit bounds it states.
