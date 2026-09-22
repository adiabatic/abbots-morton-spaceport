# The build fleet

Nothing in the build reads this file — every width derives from the box in hand at run time (`rebuild/tools/memory_budget.py` is the probe and the arithmetic, and the per-unit costs live at their call sites) — so this is the reference for reading host-tagged output: `make cycle-timings` rows, `make job-costs`' "width here" lines, and any prose that says "the 32 GiB Mac" or "the 48 GiB box". The hostnames themselves stay out of the repo; match a timings row to its machine by the cores and RAM the row itself carries.

| Machine            | Chip   | CPU cores                         | RAM                         |
| ------------------ | ------ | --------------------------------- | --------------------------- |
| MacBook Pro        | M5 Pro | 18 — 6 super + 12 performance     | 48 GiB (prints as 51.54 GB) |
| Mac mini (2024)    | M4 Pro | 12 — 4 efficiency + 8 performance | 48 GiB (prints as 51.54 GB) |
| MacBook Pro (2021) | M1 Pro | 10 — 2 efficiency + 8 performance | 32 GiB (prints as 34.36 GB) |

- The tools print decimal gigabytes, which is why 48 GiB reads as 51.54 GB and 32 GiB as 34.36 GB. The two RAM sizes appear in `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` as `BOX_48_GIB` and `BOX_32_GIB`, in bytes, so width assertions — the kernel delta wave's whole-wave criterion and the surface width among them — run against fleet capacities rather than invented capacities.
- The M5 Pro row uses Apple's [super/performance terminology](https://www.apple.com/newsroom/2026/03/apple-debuts-m5-pro-and-m5-max-to-supercharge-the-most-demanding-pro-workflows/): super cores are the fastest tier, and performance cores use a distinct design optimized for efficient multithreaded work.
- `memory_budget.usable_cores()` counts all available CPU cores, and no budget weights core classes, so a width of N may span different core classes.
- Which widths each box actually gets is deliberately not recorded here — that is what `make job-costs`' "width here" lines and `make cycle-timings` answer on the machine itself, from the constants as they stand that day.
