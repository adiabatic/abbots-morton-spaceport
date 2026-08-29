# The build fleet

Nothing in the build reads this file — every width derives from the box in hand at run time (`rebuild/tools/memory_budget.py` is the probe and the arithmetic, and the per-unit costs live at their call sites) — so this is the reference for reading host-tagged output: `make cycle-timings` rows, `make job-costs`' "width here" lines, and any prose that says "the 32 GiB Mac" or "the 48 GiB box". The hostnames themselves stay out of the repo; match a timings row to its machine by the cores and RAM the row itself carries.

| Machine            | Chip   | CPU cores      | RAM                         |
| ------------------ | ------ | -------------- | --------------------------- |
| Mac mini (2024)    | M4 Pro | 12 — 8 P + 4 E | 48 GiB (prints as 51.54 GB) |
| MacBook Pro (2021) | M1 Pro | 10 — 8 P + 2 E | 32 GiB (prints as 34.36 GB) |

- The tools print decimal gigabytes, which is why 48 GiB reads as 51.54 GB and 32 GiB as 34.36 GB. The same two machines appear in `rebuild/test_artifact_cycle.py` as `BOX_48_GIB` and `BOX_32_GIB`, in bytes, so width assertions run against the fleet rather than against invented hardware.
- `memory_budget.usable_cores()` counts performance and efficiency cores alike, and no budget weights them, so a width of N may be scheduled partly on E-cores.
- Which widths each box actually gets is deliberately not recorded here — that is what `make job-costs`' "width here" lines and `make cycle-timings` answer on the machine itself, from the constants as they stand that day.
