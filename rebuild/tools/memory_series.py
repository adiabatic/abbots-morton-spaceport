"""The memory series: measure what the M1 table build, the string replay and the review corpus build hold and write at each of several alphabet cuts, and what a settle-memo absorb holds at each of several row counts, one heavy process at a time, so a change that claims a term is flat shows it across the cuts instead of at the whole alphabet alone. It has the scaling sweep's shape (`rebuild/tools/scaling_sweep.py`), which times one configuration, and measures the whole build instead. It measures one holder a run, which `--holder` picks: the table build (`tables`, the default), whose step the paragraphs up to "The corpus step" describe, the corpus build (`corpus`), whose step they then describe, the string replay (`replay`), whose step "The replay step" describes, or the oracle's and the conformance sweep's settle-memo absorb (`absorbs`), whose step "The absorbs step" describes.

Each table step is one `build-tables` over the configurations named (every settlement configuration unless `--configs` narrows them) on one alphabet cut, at the flags a bare run_m1 derives on this machine (`bare_flags`, as `run_m1.build_tables` derives them with nothing beside the build: the width and how many deltas start from scratch beside `default`), unless `--kernel-threads` or `--scratch-beside-default` states one. Every step runs the same flags, so the cuts differ only in their alphabet. The cut is `scaling_series.sub_spec`'s, over `scaling_series.series_order`, the rule the alphabet switch (`spec_load.M1_RUNES_ENV`) narrows by too, so a line's `alphabet` is what that switch takes to build the same cut in a tree of the fault kit's kind. The sizes default to `SIZES` below the whole alphabet and the whole alphabet itself, so the largest line is the live build's; positional arguments name other rune counts, and a count named twice is measured twice.

A step's build writes no file. Each configuration's windows payload goes through a FIFO at its path and is counted as it arrives (`scaling_sweep.scan`, through `kernel_exec.stream_payloads`). The settlement and joins paths are symbolic links to the null device. The crate opens every one of these paths with `File::create`, which opens a FIFO or the null device and truncates neither. The crate's stdout and stderr come back through pipes. The `--inputs` stamp is `STAMP_PREFIX` and the rune count, so one cut hashes the same from run to run.

A table line holds, beside `step` (`tables`) and the cut (`runes`, `letters`, `ligs`, `configs`, the two flags and `modes`, the settlement modes `kernel_exec.mode_flags` passes):

- `wall_s` and `cpu_s`: the child's wall time and its own user and system time, from the rusage `os.wait4` reaps it with.
- `peak_resident` and `peak_footprint`: the child's resident peak from that rusage and its lifetime peak footprint, read between its exit and its reap as `peak_rss.reap_peaks_bytes` reads it. This module reaps the child itself, because that function discards the rusage the CPU figure comes from.
- `start`, `peak` and `end`: a sampler thread reads the child's resident set and footprint (`peak_rss.resident_bytes`, `peak_rss.footprint_bytes`) and the machine (`read_machine`: swap in use, `peak_rss.swap_used_bytes`; the compressor's occupancy, `peak_rss.parse_compressor_bytes`, and the swapout count, `peak_rss.parse_swapouts`, both over `vm_stat`; and the internal SSD's write counter, as the write meter reads it) every `--interval` seconds. `start` is the machine just before the spawn, `peak` the sample whose footprint was highest, and `end` the machine once the child is reaped beside the last sample's child figures, whose footprint reads None when that sample caught the child exiting (`peak_rss.footprint_bytes`). `compressor_max` and `swap_used_max` are the highest of every reading, and `samples` how many the sampler took.
- `windows`: each configuration's `[windows, rules, cells]`, counted from its payload as `scaling_sweep.counts` counts them. `digests`: each configuration's table digest from the child's stdout.
- `phases`: the child's `[t]` lines, label to seconds. `tables`: the `[c]` lines that give a collection's length and capacity, as `[len, cap, load]` by configuration and collection, the load being length over capacity. `resident`: the `[c]` lines that give a resident size, in bytes. `stats`: every other `[c]` line's fields (`cache_lines` says how a line is keyed).
- `written`: the write meter's fields (`rebuild/tools/write_meter.py`). The meter is read with the device `--idle-seconds` before the step and again just before the step's FIFOs are made, which gives the device's idle rate (`idle_rate`); then once the child is reaped and every FIFO drained (`end`), and again `--after-seconds` after that (`after`), since a write the kernel defers and the swap the pressure leaves reach the device late. Each of the two blocks reads from the reading just before the FIFOs are made: `basis`, `written` (the coalition's logical writes, `WriteDelta.bytes`), `diskio`, `device`, `device_net` (the device's delta less the idle rate over the same seconds), `swap` (the VM volume's writes), `swapouts` (the `vm_stat` swapout delta from `start`), `seconds`, and the meter's `warning` when it has one. `vm.swapusage` lags and does not fall back once the pressure ends, so the swapout delta is the swap reading a step owns. The window from that reading to `end` holds the build, the machine reading before its spawn, and the making and removal of the step's FIFOs, which come to a few kilobytes of directory metadata; it holds neither the step directory's making nor its removal. Before the first step's idle reading the run flushes its own setup's writes (the crate's build, the binary's copy, the spec dumps, the index refresh `git describe` makes and the record's truncation) with `os.sync` and waits `SETTLE_S` for them to reach the device, so the first step's idle rate holds none of them. A basis other than `own` or a warning also prints a `[warn]` line.
- `alphabet` (the cut's rune names), `cpus` and `ram` (`memory_budget.usable_cores` and `total_memory_bytes`, which `doc/fleet.md` matches to a machine; no hostname is recorded), `head` (the commit, with `-dirty` after it when tracked files have changed) and `finished_at`.

The lines' writes read in the own basis only when nothing outside the series shares its resource coalition, which is what `make memory-series` gives it: it runs the series as a one-shot launchd job's own process (`rebuild/tools/job_launch.py`), as `make fault-kit` runs the kit, and follows its log in `tmp/memory-series.log`. Run another way, the series still runs, and each line's basis says what its figure is.

The series works in its own tree, `TREE_PATH` under this checkout's `var/perf/`, never in the live tree, and empties it as a run starts (`empty_tree`). For the table holder `prepare_tree` then copies the crate's release binary into it once `kernel_exec.cargo_build` has built it, so a crate rebuilt elsewhere during the series changes nothing it measures, and every cut's spec dump (`spec-r<runes>.json`) is written there before the first step, so a rune edited during the series changes nothing either. Each table step's directory, `r<runes>`, holds only its FIFOs and links and is removed when the step ends. A run holds `<tree>.lock` beside the tree (`store_lock.hold_flock`) and refuses when another run holds it, since two series would share the tree and each other's memory. It refuses too while the alphabet switch is set, because the series cuts its alphabets from the whole store itself; it never sets the switch. SIGINT, SIGTERM and SIGHUP stop a run as they stop a cycle pass (`artifact_cycle.stop_signals`), which matters because `job_launch` forwards each to the series alone: the running step's child is killed (a corpus line's with its whole process group), the step's directory and the corpus step's clones are removed, the lock is released, and the run exits with 128 plus the signal's number.

Each line prints to stdout as its step finishes and goes to `--out`, `OUT_PATH` by default, the checked-in record of each holder's last run. A run replaces only its own holder's lines and fit there and keeps every other holder's as the file held them, holders in `HOLDERS` order (`Record`), rewriting the file as each line finishes. After a table run's last step both get the whole-series fit against runes of the windows, the peak footprint and resident, the CPU time and the first configuration's table lengths (`report`), the lengths rather than the table bytes, because a hash table's bytes move in doublings.

The corpus step builds the review corpus with `build.build_m1`, called as a library in a child process, once a line: `rebuild/tools/memory_series_child.py`, run as its own module, so the build's parent and its pool workers import the build's modules and not this module's (that module's docstring says why). A cut line, `r<runes>`, builds over the live divergence audit cut to `scaling_series.sub_spec`'s alphabet at that rune count: a row is kept when every Quikscript code point of its window is one of the cut's letters (`cut_pattern`), which gives the audit run_m1 writes at that alphabet byte for byte. The cuts default to `CORPUS_SIZES` below the whole alphabet and the whole alphabet. Every cut line is a full build, each unit recomputed, since its out dir holds no unit store. A load line, `load-x1` or `load-x2`, runs the child over the whole alphabet on the live audit (×1) or on the live audit followed by its moved copy (×2, `moved_row`: each Quikscript code point moved up by `MOVED` and both rendered-name fields tagged), and the child's mark reporter stops the build at the plan's mark (`PLAN_MARK`), before any worker or output exists. Both load lines extend `build.LETTERS` to the moved code points, copy each family's content key to its tagged name, and skip the signature resolution and the ink-duplicate merge, which a moved window has no signature for, so the two compare like with like. Positional arguments name the lines (`30`, `load-x1`); with none, a corpus run measures every cut and both load lines. The widths are the build's own defaults on this machine (`corpus_widths`) at every line, unless `--jobs`, `--signature-jobs` or `--write-jobs` states one.

The corpus step reads the live tree only through clones it makes as a run starts (`clone_inputs`, `clonefile(2)` through `build._clone`, which writes no data) under `<tree>/corpus/inputs/`: the audit, the subset tables and their pack, the M1 font and its Stage A record, the live corpus's manifest and ink-signature store, the spec root's paths (`SPEC_PATHS`, the ledger among them) and the two site fonts. A clone the volume refuses stops the run, since a copy would write gigabytes. A run refuses while the debug tally's switch (`memory_tally.TALLY_ENV`) is set, since the tally keeps alive the collections it reads, and `check_inputs` refuses when the live corpus was not built over the M1 outputs' Stage A record, since its signature store would then miss and the load would shape, or when the pack is not current for the tables, since the build would rewrite it. The child runs from this checkout, so its units workers run the checkout's code and crate binary, and each line reads their digests (`corpus_code`) just before the spawn, once its idle wait is over, and again as soon as the child is reaped, before the wait for the meter's last reading, so the two readings bracket the build and none of the line's waits; a digest that moves between them, or from the first line's to a later line's first reading, prints a `[warn]` line naming the line. An edit made during a line's waits shows at the next line, the first whose build runs it. As the run ends, however it ends, it removes `<tree>/corpus/`, the clones with it, since once the live tree's next pass replaces those files the clones alone would keep their blocks allocated.

Each corpus line's directory, `<tree>/corpus/<line>`, holds the audit's FIFO, which a harness thread fills from the cloned audit (`feed_audit`), the build's out dir, `out/`, the temporary directory the child's `TMPDIR` names, `temp/`, and `pools.ndjson`, where the child points `cycle_timings.JOURNAL`, so the build's pool records land there and not in the live journal. The audit comes through a FIFO because `audit._stage_audit` reads it once, front to back; a build that reads it by `pread` from one descriptor, as #618's change 15 has it, needs each cut's audit as a real, seekable file instead. A cut line's out dir holds a clone of the live signature store, which the build's load reads, and a link to the null device at the staging name of every output the build writes and never reads back (`null_routes`): each shard part a class can take, the units index, the three app sidecars, the unit store's head and parts and the signature store's rewrite. The build writes for real only what it reads back or what is too small to route: the manifest, the fonts, the static files and `review-facts.json`. Beside them, each process that settles windows, every units worker, writes one dump of the whole alphabet's spec into `temp/` (`kernel_exec._spec_dump`; every line settles over the whole spec, so the dump is the same at every cut) and removes it as it exits, and runs `kernel_exec.ensure_built`'s `cargo build`, which rewrites the crate's dep-info files under `rebuild/kernel-rs/target/`, outside the line's directory, even when it builds nothing. Those writes are part of how far a cut line's `written` exceeds its `files.kept`, not all of it: `written` counts every byte the coalition writes over the line's window, and `files` only the files the line leaves, so a file written outside the line's directory, or written and removed during the build, counts in the first alone. Once the child is reaped the step lists every regular file the line left (`step_files`); any other is a stray, which the line records under `files` and which stops the run once the line is in the record. The directory is removed when the line ends.

A corpus line holds, beside `step` (`corpus` or `corpus-load` with its `scale`), the cut (`runes`, `letters`, `ligs`, `alphabet`), the widths the child was given (`jobs`, `signature_jobs`, `write_jobs`) and those that ran (`units_width` and `write_width`, from the phase notes, and `handout`, `_handout_width`'s batch):

- `rows`, the audit rows the harness fed, and `feed_s`, the seconds after the spawn at which it closed the FIFO. The harness scans the whole cloned audit at every cut, so a load whose `stage_audit returned` mark arrives soon after `feed_s` waited on that scan, and a small cut's load seconds include it. `units_premerge` and `units`, the units the build loaded and those left after the ink-duplicate merge; `windows`, the distinct windows; `human_units`, `recomputed`, `classes` and `write_runs`, each write run's classes and units as `build._write_runs` cuts them from the line's manifest at the width the write ran at.
- `wall_s`, `peak_footprint`, `start`, `peak`, `end`, `compressor_max`, `swap_used_max`, `samples` and `interval_s`, as a table line holds them, of the build's parent. `cpu_s` and `peak_resident` are the parent's own rusage, which the child prints as it exits (`[r]`), since the rusage `os.wait4` returns adds the reaped workers'; `children` holds their CPU and largest resident peak from the parent's `RUSAGE_CHILDREN`.
- `marks`: one entry a sub-step of the parent, from each `memory_tally.mark` the build makes (`CORPUS_MARKS` lists them in order), with the parent's resident and footprint at the mark, the highest of each a sampler of the parent alone read since the previous mark (every `parent_interval_s`, `PARENT_INTERVAL_S`, `parent_samples` times), and the mark's counts. The labels follow those of #618's corpus load probe, so a load line compares with its `load-x1.json` and `load-x2.json`.
- `phases`: each `[t] review.build <phase>` line's seconds, `rss_gb`, `rss_now_gb` and note. `signatures_shaped` is the load note's shaped count, which is 0 when the cloned store answers every window, and a cut line that shaped prints a `[warn]` line.
- `pools`: for the signature, units and write pools, the width and each worker's peak from the build's pool records (`reading` names whether they are footprints), the parent's as the controller, and each worker's resident peak as the sampler read it every `--interval` seconds (`resident`), with the median and top of each. The sampler reads every process below the parent. A child of the parent that runs multiprocessing's spawn entry is a pool's worker, of the pool between whose closing mark and the previous pool's it first appears (`POOL_MARKS`); every other process but multiprocessing's resource tracker, the git and crate calls the parent and its workers make, is summed under `subprocesses`.
- `fragments`: the units phase note's recomputed units and the arena's size and ratio; `sidecar_lines_mb`, each write run's compressed sidecar lines, from the write phase's note.
- `written`, as a table line holds it, and `files`: the regular files the line left, with their sizes, under `kept`, and any other under `stray`.
- `inputs` (the Stage A record and the live corpus's `generated_at`), `allocator_env` (the child's `Malloc*` variables), `code` (the digests just before the spawn) and, when they moved during the build, `code_after` (once the child is reaped), `cpus`, `ram`, `head` and `finished_at`.

After a corpus run's last line the record and stdout get the fit of each corpus figure against units, and of the parent's footprint at the load's and the plan's marks against rows, over the cut lines, and the ×2 load line's ratio to the ×1 line at each mark (`corpus_report`).

The replay step measures a full string replay as run_m1 runs one (`run_m1.replay_and_absorb`): every configuration named (every settlement configuration unless `--configs` narrows them) walked over every text to `run_m1.REPLAY_MAX_LENGTH`, one `replay-strings` process a configuration at most `--replay-threads` at once (the width a bare run_m1 derives, `run_m1._replay_threads`, by default), each beside the spawn worker that absorbs its window memo from a FIFO into the settle memo file's columns, builds its index and holds the finished file until every walk has passed, or below the configuration count drops it at once while a walk still waits for a slot. The cuts default to `REPLAY_SIZES` below the whole alphabet and the whole alphabet; positional arguments name others, and a count named twice is measured twice. For each distinct cut the run first builds the tables once, unmeasured (`build_replay_tables`): one `build-tables` at the table holder's flags whose settlement and joins tables are written plain under `<tree>/replay/r<runes>/`, since the walks read them, about 0.05 GB a cut, and whose windows payloads are drained from FIFOs; it writes the cut's spec dump and a pickle of the cut beside the tree's other spec dumps, and removes the tables as the run ends, however it ends. Each line then runs the replay in a child, `rebuild/tools/memory_series_child.py`'s `replay` entry, so the absorb workers import run_m1's modules and the child's and not this module's. The child runs with this process's environment as a corpus child takes it (`corpus_environment`), and takes this process's built kernel as its own (`--built`), so no `cargo build` runs inside a line. KERNEL_ALLOCATOR_ENVIRONMENT reaches the walks alone, as in run_m1, since `kernel_exec.replay_memo_walk` adds it to each crate's environment and the absorb workers are Python processes: each crate runs with `--cache-stats`, and each absorb hashes its finished file's bytes in order and then discards it, as a red replay does, so a line writes no settle memo and nothing else. The walks and absorbs run the checkout's crate binary and code, as run_m1's do.

A replay line holds, beside `step` (`replay`), the cut (`runes`, `letters`, `ligs`, `alphabet`), `configs`, `width`, `max_length` and `modes` (the settlement flags the walks take):

- `walks`: for each configuration, the walk's `counts`; its crate process's `peak_resident`, `peak_footprint` and `cpu_s` under `walk`, from the rusage `os.wait4` reaps it with and its footprint read before the reap (`peak_rss.ReapedPeaks`); its absorb worker's own `peak_resident`, `peak_footprint` and `cpu_s` under `absorb`, which the worker reads of itself as it ends, beside the absorbed file's `rows`, `bytes` and `sha256`, the seconds its read and index took and any warning; `pair`, the walk's crate and its absorb worker summed at each sample, the highest resident sum and the highest footprint sum; `phases`, the walk's `[t]` lines; and `cache`, from its `[c]` lines (`replay_cache`): the unit count, the largest unit's tables and its resident size after its release, the side memo, the row store and `resident_after_walk`.
- `combined`: every walk's crate and every absorb worker summed at each sample, the highest resident sum and the highest footprint sum, and `start`, `peak` and `end` as a table line holds them, the peak being the sample of the highest combined footprint, with `compressor_max`, `swap_used_max`, `samples`, `interval_s` and `wall_s`. The sampler reads the machine and every process below the child every `--interval` seconds (`_ReplayTree`), and tells each walk's crate by the configuration its command line names and each absorb worker by the pid its walk reports.
- `written`, as a table line holds it, and `cpus`, `ram`, `head` and `finished_at`.

After a replay run's last line the record and stdout get the fit against runes of the windows, the walk, absorb, pair and combined peaks, the walks' CPU time, the row store and the largest unit's walk memo (`replay_report`).

The absorbs step measures one settle-memo absorb (`conform.absorb_settle_memo`) a line, over inputs made from the live `default` settle memo at a row count, and beside it the carry-and-append absorb its bytes are proven against over the same inputs (`memory_series_child.carry_and_append_absorb`). The run clones the live memo into `<tree>/absorbs/` with `clonefile(2)` (`build._clone`, which writes no data) and maps the clone. Positional arguments name the row counts, `live` for the live memo's own rows (3,649,648 at 45 runes); the default is `ABSORB_COUNTS`, and a count named twice is measured twice. For each distinct count the run makes the inputs once (`absorb_count`): the `live` count's standing file is the clone, and each other count's is one file written once a run from the live rows, a prefix of them below the live count and, above it, the live rows followed by the live rows restated under labels of their own, `.r<copy>` after every label, so each restated window is one no other row holds and names the same family (`_CountRows`). Such a file holds 14 bytes a row of columns and 4 bytes a slot of index at 2 to 4 slots a row, about 0.02 GB at a million rows, 0.26 GB at nine million and 0.30 GB at twelve million, about 0.6 GB a run at the default counts, and the run removes it as it ends, however it ends. Beside it the run holds each count's fresh windows compressed in memory, as a walk hands them (`conform.FreshMemo`): the standing rows' last `ABSORB_TAIL` rows restating their own windows, the last rows again under labels the file lacks, as many as the file's index has room for beside its own rows (all `ABSORB_TAIL` at the live count, 48,576 at a million rows), each in `ABSORB_RANGES` row ranges, and every row in the `default` sweep's unit count of parts. `--paths` names the paths a count's lines read (`absorb_job`), every one of `ABSORB_PATHS` by default: `fast`, the standing file and the new windows, as many as its index has room for, so its absorb reuses the standing index; `duplicate`, the standing file and the restated windows, each patching a standing outcome; `stale`, the same with `ABSORB_STALE_FAMILY`'s key moved, which drops rows and rebuilds the index; `prune`, the standing file's last `ABSORB_PRUNED` rows unreached and no fresh row, through the sweep's absorb; `fresh`, no standing file and every row fresh in the units' parts, through the oracle's absorb; and `fresh-sweep`, the same through the sweep's. The units' parts split the rows disjointly, so no fresh line merges a row, and none reads the merge state an all-fresh pass's repeated windows bring (`conform.absorb_settle_memo`). A count's standing file, and its tail and new windows, are made only when `--paths` names a path that reads them, so a path that needs no standing file writes none. Each line ships its parts' blocks into POSIX shared memory segments as the pools do, naming each in `ABSORB_SEGMENTS` under the run's directory before making it (`_AbsorbSegments`), and unlinks every one once its two absorbs have run, or however the line ends; a run killed with SIGKILL leaves that record, and the next run's `empty_tree` unlinks the segments it names.

Each line runs two children of `rebuild/tools/memory_series_child.py`'s `absorb` entry, one after the other, each a fresh process sampled as a table step's crate is (`run_absorb_child`): the absorb, then the carry-and-append absorb. Each child gets its own copy of the line's fresh windows in POSIX shared memory segments the harness makes, one a part, and its job (the file, the spec, the segments' names and the parts' counts, and the sweep's prune) pickled on its stdin, as the oracle's and the sweep's pools hand an absorb worker its arguments; it maps each segment and unlinks its name at once, runs its absorb with the finished file hashed in order instead of written, and prints what it did. A line writes nothing but its record. An absorbs line holds, beside `step` (`absorbs`), the count (`count`, `rows`, `standing_rows`, `fresh_rows`, `standing_bytes`) and the path (`path`):

- `absorb`: the absorb's `conform.MemoAbsorb`, the row counts and index path its `[t]` lines report (`written`, `carried`, `pruned`, `fresh`, `standing`, `parts`, `patched`, `dropped`, `index`) and the slot count (`slots`).
- `wall_s`, `seconds` (the absorb's own), `cpu_s`, `peak_resident`, `peak_footprint`, `start`, `peak` and `end`, `compressor_max`, `swap_used_max`, `samples` and `interval_s`, as a table line holds them, of the absorb's child.
- `file_rows`, `size` and `sha256` of the file the absorb would write; `carry_and_append`, the same figures and the child's peaks, CPU and seconds for the carry-and-append absorb; and `same`, whether the two files' digests are equal. A line whose files differ prints a `[warn]` line.
- `written`, as a table line holds it, over both children, and `cpus`, `ram`, `head` and `finished_at`.

After an absorbs run's last line the record and stdout get each path's fit against rows of the absorb's peak footprint and resident and the carry-and-append absorb's peak footprint (`absorbs_report`).

Run as: make memory-series ARGS='[--holder tables] [k ...] [--configs a,b] [--kernel-threads N] [--scratch-beside-default N] [--idle-seconds S] [--after-seconds S] [--interval S] [--out FILE]', ARGS='--holder replay [k ...] [--configs a,b] [--replay-threads W] [--idle-seconds S] [--after-seconds S] [--interval S] [--out FILE]', ARGS='--holder corpus [k ...] [load-x1] [load-x2] [--jobs N] [--signature-jobs N] [--write-jobs N] [--idle-seconds S] [--after-seconds S] [--interval S] [--out FILE]', or ARGS='--holder absorbs [rows|live ...] [--paths a,b] [--idle-seconds S] [--after-seconds S] [--interval S] [--out FILE]'.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import functools
import json
import os
import pickle
import re
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
import zlib
from array import array
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, BinaryIO, cast

from rebuild.pipeline import conform, kernel_exec, kernel_io, oracle, run_m1, spec_load
from rebuild.pipeline.model import ResolvedSpec
from rebuild.review import store_lock
from rebuild.tools import (
    artifact_cycle,
    memory_budget,
    memory_series_child,
    memory_tally,
    peak_rss,
    scaling_series,
    scaling_sweep,
    write_meter,
)
from rebuild.tools.console import INNER_LINE, WARN
from rebuild.tools.kernel_all_configs import configs_from
from rebuild.tools.memory_series_child import MOVED, PLAN_MARK, RUSAGE, CorpusInputs

ROOT = Path(__file__).resolve().parents[2]
TREE_PATH = "var/perf/series"
OUT_PATH = "rebuild/memory-series.txt"
BINARY_NAME = "ams-m1-kernel"
SIZES = (34, 38, 41, 43, 44, 45)
IDLE_S = 60.0
AFTER_S = 300.0
SETTLE_S = 10.0
INTERVAL_S = 0.5
STAMP_PREFIX = "memory-series-r"
VM_STAT = "/usr/bin/vm_stat"
READ_CHUNK_BYTES = 1 << 20
WINDOWS = "windows"
# A `[c]` line's fields that say which instance of a collection it reports, such as `shared_memo_hits id=0` or a replay walk's `walk_memo unit=3`, rather than a figure.
CACHE_QUALIFIERS = ("id", "release", "unit")
_CACHE_LINE = re.compile(r"^\[c\] (\S+) (.+)$", re.MULTILINE)

TABLES = "tables"
CORPUS = "corpus"
REPLAY = "replay"
ABSORBS = "absorbs"
HOLDERS = (TABLES, CORPUS, REPLAY, ABSORBS)
CORPUS_STEP = "corpus"
LOAD_STEP = "corpus-load"
REPLAY_STEP = "replay"
ABSORB_STEP = "absorbs"
HOLDER_OF_STEP = {
    TABLES: TABLES,
    CORPUS_STEP: CORPUS,
    LOAD_STEP: CORPUS,
    REPLAY_STEP: REPLAY,
    ABSORB_STEP: ABSORBS,
}
# The absorbs holder's row counts: `live` names the live `default` settle memo's own rows.
LIVE = "live"
ABSORB_COUNTS = ("1000000", LIVE, "9000000", "12000000")
ABSORB_PATHS = ("fast", "duplicate", "stale", "prune", "fresh", "fresh-sweep")
ABSORB_CONFIG = "default"
ABSORB_TAIL = 170_000
ABSORB_RANGES = 2
ABSORB_PRUNED = 20_000
ABSORB_STALE_FAMILY = "qsI"
ABSORB_SEGMENTS = "segments"
REPLAY_SIZES = (34, 38, 41)
CORPUS_SIZES = (30, 34, 38, 41, 43, 44)
LOAD_SCALES = (1, 2)
LOAD_PREFIX = "load-x"
PARENT_INTERVAL_S = 0.1
LOAD_MARK = "compacted"
CORPUS_MARKS = (
    "pack",
    "stage_audit returned",
    "triage permutation built",
    "load_workload done",
    "spec loaded",
    "family keys",
    "signatures resolved",
    "premerge captured",
    "merged",
    LOAD_MARK,
    "unit store allocated",
    "input keys written",
    "rows released",
    "named built",
    "named freed",
    PLAN_MARK,
    "units pool started",
    "units drafted",
    "names released",
    "id index built",
    "triage sorted",
    "duplicate groups",
    "by_codepoints built",
    "by_codepoints freed",
    "verified",
    "units pool stopped",
    "write pool started",
    "manifest written",
    "sidecars written",
    "write pool stopped",
    "review facts written",
    "unit store written",
)
# Each pool's name in a line, its unit in the build's pool records, and the mark by which every one of its workers has started. A pool's workers start after the previous pool's mark, since the build runs its pools one after another.
POOL_MARKS = (
    ("signature", "signature", "signatures resolved"),
    ("units", "corpus", "units pool stopped"),
    ("write", "corpus-write", "write pool stopped"),
)
# The paths `enrich.load_spec` and `fingerprint.stage_b` read under a spec root, the ledger among them, as the mini bundle's pin lays them out.
SPEC_PATHS = ("glyph_data/runes", "rebuild/script.yaml", "rebuild/schema", "rebuild/m1-divergences.yaml")
AUDIT_NAME = "divergence-audit.tsv"
OUT_NAME = "out"
POOLS_NAME = "pools.ndjson"
TMP_NAME = "temp"
FIFO_POLL_S = 0.05
CHILD_PIDS_MAX = 4096
WORKER = "multiprocessing.spawn import spawn_main"
TRACKER = "multiprocessing.resource_tracker"
_MARK_LINE = re.compile(r"^\[m\] (.+?)\t(.*)$")
_SIGNATURE_NOTE = re.compile(r"signatures: ([\d,]+) cached, ([\d,]+) shaped")
_JOBS_NOTE = re.compile(r"\bjobs=(\d+)")
_RECOMPUTED_NOTE = re.compile(r"\brecomputed=([\d,]+)")
_ARENA_NOTE = re.compile(r"arena ([\d.]+) GB, deflated ([\d.]+) to 1")
_SIDECAR_NOTE = re.compile(r"sidecar lines ([\d., ]+) MB by run")


@dataclass(frozen=True)
class Flags:
    """The table build's width and how many deltas it starts from scratch beside `default`."""

    threads: int
    scratch_beside_default: int


@dataclass(frozen=True)
class Machine:
    """One reading of the machine: swap in use, the compressor's occupancy in bytes, the swapout count in pages, and the internal SSD's write counter, each None where it cannot be read."""

    swap_used: int | None
    compressor: int | None
    swapouts: int | None
    device: int | None


QUIET = Machine(None, None, None, None)


@dataclass(frozen=True)
class Sample:
    """One sample of a running child: seconds since its spawn, its resident set and footprint, and the machine."""

    at: float
    resident: int | None
    footprint: int | None
    machine: Machine


@dataclass(frozen=True)
class ChildRun:
    """What `run_child` returns: the child's exit status, stdout and stderr, wall and CPU seconds, resident and footprint peaks, the machine before its spawn, its samples, and the machine after its reap."""

    returncode: int
    stdout: bytes
    stderr: str
    wall: float
    cpu: float
    peak_resident: int
    peak_footprint: int | None
    begin: Machine
    samples: tuple[Sample, ...]
    end: Machine


def default_sizes(count: int, sizes: Sequence[int] = SIZES) -> list[int]:
    """The rune counts a run cuts at when none is named: `count`, the whole alphabet's size, and each of `sizes` below it, `SIZES` for the table holder, `REPLAY_SIZES` for the replay holder and `CORPUS_SIZES` for the corpus holder."""
    return sorted({*(size for size in sizes if size < count), count})


def bare_flags(
    configs: Sequence[str],
    *,
    threads: int | None = None,
    scratch: int | None = None,
) -> Flags:
    """The flags a bare run_m1 builds `configs` at on this machine, derived as `run_m1.build_tables` derives them with nothing beside the build, each replaced by the one stated. A stated width is capped at the configuration count and the cores, as run_m1 caps it."""
    from_scratch = "default" not in configs
    width = run_m1._table_build_threads(threads, len(configs), from_scratch=from_scratch)
    if scratch is None:
        scratch = kernel_exec.deltas_from_scratch(width, configs=len(configs), from_scratch=from_scratch)
    return Flags(width, scratch)


def step_arguments(
    binary: Path, spec_path: Path, out_dir: Path, configs: Sequence[str], stamp: str, flags: Flags
) -> list[str]:
    """The `build-tables` command line of one step, with its cache stats and timings on and its windows stamped `stamp`."""
    arguments = [
        str(binary),
        "build-tables",
        str(spec_path),
        str(out_dir),
        f"--configs={','.join(configs)}",
        f"--inputs={stamp}",
        f"--threads={flags.threads}",
        *kernel_exec.mode_flags(),
        "--cache-stats",
        "--timings",
    ]
    if flags.scratch_beside_default:
        arguments.append(f"--scratch-beside-default={flags.scratch_beside_default}")
    return arguments


def route(out_dir: Path, configs: Sequence[str]) -> dict[str, Path]:
    """Make `out_dir`, which must not exist, with each configuration's settlement and joins paths linked to the null device, and return the paths `kernel_exec.stream_payloads` makes FIFOs at: each configuration's windows payload, keyed `windows <config>`."""
    out_dir.mkdir(parents=True)
    paths: dict[str, Path] = {}
    for config in configs:
        settlement, joins = run_m1.config_table_files(out_dir, config)[:2]
        settlement.symlink_to(os.devnull)
        joins.symlink_to(os.devnull)
        paths[f"{WINDOWS} {config}"] = kernel_exec.windows_payload(out_dir, config)
    return paths


def _vm_stat() -> str | None:
    try:
        return subprocess.run([VM_STAT], capture_output=True, text=True, check=True, timeout=30).stdout
    except OSError, subprocess.SubprocessError:
        return None


def read_machine() -> Machine:
    """Read the machine (`Machine`): swap in use from `peak_rss.swap_used_bytes`, the compressor's occupancy and the swapout count from one `vm_stat`, and the device's write counter from the `ioreg` run the write meter makes."""
    text = _vm_stat()
    device, _swap = write_meter._read_device()
    return Machine(
        peak_rss.swap_used_bytes(),
        peak_rss.parse_compressor_bytes(text),
        peak_rss.parse_swapouts(text),
        device,
    )


class Sampler:
    """A daemon thread that samples one child (`Sample`) every `interval` seconds, from `start` until `stop`, which returns the samples. `tick`, when given, is called after each sample with its time, which the corpus step reads the child's descendants on."""

    def __init__(
        self,
        pid: int,
        interval: float,
        machine: Callable[[], Machine],
        origin: float,
        tick: Callable[[float], None] | None = None,
    ) -> None:
        self._pid = pid
        self._interval = interval
        self._machine = machine
        self._origin = origin
        self._tick = tick
        self._stopped = threading.Event()
        self._samples: list[Sample] = []
        self._thread = threading.Thread(target=self._run, name=f"sampler {pid}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while True:
            at = time.perf_counter() - self._origin
            resident = peak_rss.resident_bytes(self._pid)
            footprint = peak_rss.footprint_bytes(self._pid)
            self._samples.append(Sample(at, resident, footprint, self._machine()))
            if self._tick is not None:
                self._tick(at)
            if self._stopped.wait(self._interval):
                return

    def stop(self) -> tuple[Sample, ...]:
        self._stopped.set()
        if self._thread.ident is not None:
            self._thread.join()
        return tuple(self._samples)


def _drain(drained: dict[str, bytes], name: str, stream: IO[bytes]) -> None:
    with stream:
        drained[name] = stream.read()


def run_child(
    arguments: Sequence[str],
    *,
    env: Mapping[str, str] | None,
    interval: float,
    machine: Callable[[], Machine] = read_machine,
) -> ChildRun:
    """Run one kernel child with its stdout and stderr on pipes drained by threads of their own, sampled (`Sampler`) until it exits, and return what it did (`ChildRun`). Once both pipes close it waits for the exit without reaping (`os.waitid` with `WNOWAIT`), stops the sampler, reads the child's peak footprint, and reaps it with `os.wait4`, whose rusage gives its resident peak and CPU time. The sampler stops before the reap, so it never reads a pid the reap has freed. An interrupt kills and reaps the child before it propagates, so no crate outlives the end of its payloads' reads (`kernel_exec.stream_payloads`)."""
    begin = machine()
    origin = time.perf_counter()
    process = subprocess.Popen(
        list(arguments), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    assert process.stdout is not None and process.stderr is not None
    drained: dict[str, bytes] = {}
    readers = [
        threading.Thread(target=_drain, args=(drained, name, stream), daemon=True)
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
    ]
    sampler = Sampler(process.pid, interval, machine, origin)
    try:
        sampler.start()
        for reader in readers:
            reader.start()
        for reader in readers:
            reader.join()
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        wall = time.perf_counter() - origin
        samples = sampler.stop()
        footprint = peak_rss.peak_footprint_bytes(process.pid)
        _pid, status, usage = os.wait4(process.pid, 0)
    except BaseException:
        sampler.stop()
        process.kill()
        process.wait()
        raise
    process.returncode = os.waitstatus_to_exitcode(status)
    return ChildRun(
        process.returncode,
        drained["stdout"],
        drained["stderr"].decode(errors="replace"),
        wall,
        usage.ru_utime + usage.ru_stime,
        peak_rss.maxrss_to_bytes(usage.ru_maxrss),
        footprint,
        begin,
        samples,
        machine(),
    )


def check_child(child: ChildRun, configs: Sequence[str], arguments: Sequence[str]) -> dict[str, str]:
    """Hold the child to the CLI contract `kernel_exec.build_table_files` holds a build to and return its digests by configuration: exit 2 is a usage error, any other nonzero exit an error about the inputs, stdout one `{config, digest}` line for each configuration in the order named, and stderr on a clean exit only `[t]` and `[c]` lines."""
    if child.returncode == 2:
        raise SystemExit(
            f"the kernel rejected the invocation as a usage error: {child.stderr.strip()} ({' '.join(arguments)})"
        )
    if child.returncode != 0:
        raise SystemExit(f"the kernel exited {child.returncode} on build-tables: {child.stderr.strip()}")
    answers = [json.loads(line) for line in child.stdout.decode().splitlines()]
    if [answer.get("config") for answer in answers] != list(configs):
        raise SystemExit(
            f"build-tables answered for {[answer.get('config') for answer in answers]}, not for {list(configs)}"
        )
    stray = [line for line in child.stderr.splitlines() if line and not line.startswith(("[t] ", "[c] "))]
    if stray:
        raise SystemExit(f"the kernel wrote a line that is neither [t] nor [c] on a clean exit: {stray[0]}")
    return {answer["config"]: answer["digest"] for answer in answers}


def cache_lines(stderr: str) -> dict[str, dict[str, dict[str, object]]]:
    """Return the child's `[c]` lines by kind, each keyed by configuration and then by name: `tables` holds `[len, cap, load]` for a line whose fields are `len` and `cap`, the load being length over capacity (None at a capacity of 0), `resident` holds bytes for a line whose one field is `kb`, and `stats` holds every other line's fields. A line's name is its words that are not fields, followed by its `CACHE_QUALIFIERS` fields, so `shared_memo_hits id=0 count=130` is `stats`' `shared_memo_hits id=0` with `{"count": 130}`. A field that is not an integer stays a string."""
    kinds: dict[str, dict[str, dict[str, object]]] = {"tables": {}, "resident": {}, "stats": {}}
    for config, rest in _CACHE_LINE.findall(stderr):
        words = rest.split()
        fields = dict(word.split("=", 1) for word in words if "=" in word)
        qualifiers = [f"{key}={fields.pop(key)}" for key in CACHE_QUALIFIERS if key in fields]
        name = " ".join([*(word for word in words if "=" not in word), *qualifiers])
        values: dict[str, object] = {
            key: int(value) if value.isdecimal() else value for key, value in fields.items()
        }
        length, capacity, kb = values.get("len"), values.get("cap"), values.get("kb")
        if values.keys() == {"len", "cap"} and isinstance(length, int) and isinstance(capacity, int):
            load = round(length / capacity, 3) if capacity else None
            kinds["tables"].setdefault(config, {})[name] = [length, capacity, load]
        elif values.keys() == {"kb"} and isinstance(kb, int):
            kinds["resident"].setdefault(config, {})[name] = kb * 1024
        else:
            kinds["stats"].setdefault(config, {})[name] = values
    return kinds


def _moment(at: float, resident: int | None, footprint: int | None, machine: Machine) -> dict[str, object]:
    return {"at_s": round(at, 3), "resident": resident, "footprint": footprint, **asdict(machine)}


def _highest(values: Iterable[int | None]) -> int | None:
    return max((value for value in values if value is not None), default=None)


def moments(child: ChildRun) -> dict[str, object]:
    """Return a child's `start`, `peak` and `end` moments and the machine's highest compressor occupancy and swap in use, as the module docstring describes them. The peak is the sample with the highest footprint, or the highest resident set where no sample read a footprint."""
    samples = child.samples
    top = max(samples, key=lambda sample: sample.footprint or sample.resident or 0, default=None)
    last = samples[-1] if samples else None
    readings = [child.begin, *(sample.machine for sample in samples), child.end]
    return {
        "start": _moment(0.0, None, None, child.begin),
        "peak": None if top is None else _moment(top.at, top.resident, top.footprint, top.machine),
        "end": _moment(
            child.wall,
            None if last is None else last.resident,
            None if last is None else last.footprint,
            child.end,
        ),
        "compressor_max": _highest(reading.compressor for reading in readings),
        "swap_used_max": _highest(reading.swap_used for reading in readings),
    }


def idle_rate(earlier: write_meter.WriteReading, later: write_meter.WriteReading) -> float | None:
    """The device's write rate between two readings taken while the series ran nothing, in bytes a second, or None when either lacks the device or no time passed."""
    seconds = later.at - earlier.at
    if earlier.device is None or later.device is None or seconds <= 0:
        return None
    return (later.device - earlier.device) / seconds


def write_fields(
    start: write_meter.WriteReading,
    later: write_meter.WriteReading,
    rate: float | None,
    swapouts: int | None,
) -> dict[str, object]:
    """One `written` block (the module docstring): the meter's delta from `start` to `later`, `device_net` its device delta less `rate` over the same seconds, and the swapout delta the caller read beside it."""
    delta = write_meter.delta(start, later)
    net = None
    if rate is not None and delta.device_bytes is not None:
        net = delta.device_bytes - round(rate * delta.seconds)
    block: dict[str, object] = {
        "basis": delta.basis,
        "written": delta.bytes,
        "diskio": delta.diskio_bytes,
        "device": delta.device_bytes,
        "device_net": net,
        "swap": delta.swap_bytes,
        "swapouts": swapouts,
        "seconds": round(delta.seconds, 3),
    }
    if delta.warning is not None:
        block["warning"] = delta.warning
    return block


def _difference(earlier: int | None, later: int | None) -> int | None:
    return None if earlier is None or later is None else later - earlier


def measure_step(
    binary: Path,
    spec_path: Path,
    step_dir: Path,
    configs: Sequence[str],
    flags: Flags,
    stamp: str,
    *,
    idle_s: float = IDLE_S,
    after_s: float = AFTER_S,
    interval_s: float = INTERVAL_S,
    env: Mapping[str, str] | None = None,
    meter: Callable[[], write_meter.WriteReading] = write_meter.read,
    machine: Callable[[], Machine] = read_machine,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run one step in `step_dir`, which must not exist and is removed once the meter has read the step's end, so the window from the meter's reading just before the FIFOs are made to its reading at the end holds the FIFOs' making and removal, the machine reading before the spawn and the build, and neither the directory's making nor its removal, and return its measured fields, everything a line holds but the cut, the flags and the provenance (the module docstring lists them). `meter`, `machine` and `sleep` are the write meter, the machine reading and the wait, which a test replaces."""
    arguments = step_arguments(binary, spec_path, step_dir, configs, stamp, flags)
    try:
        paths = route(step_dir, configs)
        idle = None
        if idle_s > 0:
            idle = meter()
            sleep(idle_s)
        start = meter()
        child, payloads = kernel_exec.stream_payloads(
            paths,
            lambda _name, source: scaling_sweep.scan(source),
            lambda: run_child(arguments, env=env, interval=interval_s, machine=machine),
        )
        end = meter()
    finally:
        shutil.rmtree(step_dir, ignore_errors=True)
    rate = None if idle is None else idle_rate(idle, start)
    digests = check_child(child, configs, arguments)
    windows: dict[str, list[int]] = {}
    for config in configs:
        scanned = payloads[f"{WINDOWS} {config}"]
        assert isinstance(scanned, tuple)
        windows[config] = list(scaling_sweep.counts(scanned, kernel_exec.windows_payload(step_dir, config)))
    after: dict[str, object] | None = None
    if after_s > 0:
        sleep(after_s)
        later = meter()
        swapouts = _difference(child.begin.swapouts, machine().swapouts)
        after = write_fields(start, later, rate, swapouts)
    return {
        "wall_s": round(child.wall, 3),
        "cpu_s": round(child.cpu, 3),
        "peak_resident": child.peak_resident,
        "peak_footprint": child.peak_footprint,
        "samples": len(child.samples),
        "interval_s": interval_s,
        **moments(child),
        "windows": windows,
        "digests": digests,
        "phases": {match.group(1): float(match.group(2)) for match in INNER_LINE.finditer(child.stderr)},
        **cache_lines(child.stderr),
        "written": {
            "idle_s": idle_s,
            "idle_rate": None if rate is None else round(rate),
            "end": write_fields(start, end, rate, _difference(child.begin.swapouts, child.end.swapouts)),
            "after_s": after_s,
            "after": after,
        },
    }


def empty_tree(tree: Path) -> None:
    """Make the series' tree at `tree` and empty it of what an earlier run left, first unlinking every shared memory segment an absorbs run killed before its cleanup recorded (`_AbsorbSegments`). A symbolic link at `tree` is refused, since emptying it would empty whatever it names."""
    if tree.is_symlink():
        raise SystemExit(
            f"{tree} is a symbolic link; the memory series empties its tree and will not follow one"
        )
    tree.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        _unlink_segments((tree / ABSORBS / ABSORB_SEGMENTS).read_text(encoding="utf-8").split())
    for path in tree.iterdir():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


def prepare_tree(tree: Path, binary: Path) -> Path:
    """Empty the series' tree (`empty_tree`), copy `binary` into it, and return the copy, run once with no arguments so its first-exec scan is over before any step's clock starts."""
    empty_tree(tree)
    copy = tree / BINARY_NAME
    shutil.copy2(binary, copy)
    subprocess.run([str(copy)], capture_output=True, check=False)
    return copy


def report(lines: Sequence[Mapping]) -> list[str]:
    """Return the whole-series fit against runes (`scaling_sweep.fit`) of the windows over every configuration, the peak footprint and resident, the CPU time, and the length of each collection the first configuration's `tables` name, one figure a line. A figure a line lacks is left out of its fit."""
    if len(lines) < 2:
        return [f"the whole-series fit needs two sizes; this run has {len(lines)}"]
    runes = [line["runes"] for line in lines]
    figures: dict[str, list[float | None]] = {
        "windows": [sum(counts[0] for counts in line["windows"].values()) for line in lines],
        "peak footprint": [line["peak_footprint"] for line in lines],
        "peak resident": [line["peak_resident"] for line in lines],
        "cpu": [line["cpu_s"] for line in lines],
    }
    config = lines[0]["configs"][0]
    names = sorted({name for line in lines for name in line["tables"].get(config, {})})
    for name in names:
        figures[f"{config} {name} len"] = [
            line["tables"].get(config, {}).get(name, [None])[0] for line in lines
        ]
    width = max(len(label) for label in figures)
    return [
        f"whole-series fit over {len(lines)} sizes (runes {min(runes)}..{max(runes)})",
        *(
            f"  {label:<{width}} ~ runes^{scaling_sweep.exponent(scaling_sweep.fit(runes, values))}"
            for label, values in figures.items()
        ),
    ]


@dataclass(frozen=True)
class CorpusCut:
    """One corpus line's alphabet: its name (`r<runes>` or `load-x<scale>`), its runes, how many of them are letters, the code points of the whole alphabet's letters it leaves out, which the audit's cut drops (`cut_pattern`), and for a load line its scale."""

    name: str
    runes: tuple[str, ...]
    letters: int
    excluded: frozenset[int]
    scale: int = 1
    load: bool = False


@dataclass(frozen=True)
class Feed:
    """What the harness fed the audit's FIFO: the rows it wrote, why it stopped early when it did, and the seconds after the child's spawn at which it closed the FIFO."""

    rows: int
    error: str | None = None
    closed_s: float | None = None


@dataclass
class Descendant:
    """One process below the build's parent, as the sampler read it: its pid, its depth (1 for the parent's own child), when it first appeared, its command when it is the parent's child, and the highest resident set and footprint read of it. A pool's worker is the parent's child that runs multiprocessing's spawn entry (`WORKER`); the parent's other children are its resource tracker (`TRACKER`) and the git and crate calls it makes."""

    pid: int
    depth: int
    first_at: float
    command: str
    resident: int | None = None
    footprint: int | None = None

    def read(self, resident: int | None, footprint: int | None) -> None:
        self.resident = _highest((self.resident, resident))
        self.footprint = _highest((self.footprint, footprint))

    @property
    def worker(self) -> bool:
        return self.depth == 1 and WORKER in self.command

    @property
    def tracker(self) -> bool:
        return self.depth == 1 and TRACKER in self.command


@dataclass(frozen=True)
class CorpusRun:
    """What `run_corpus_child` returns: the child as `run_child` returns one, its stderr lines with the seconds after the spawn each arrived at, the 0.1 s trace of its resident set and footprint, every process below it, and the harness's feed."""

    child: ChildRun
    lines: tuple[tuple[float, str], ...]
    trace: tuple[Sample, ...]
    descendants: tuple[Descendant, ...]
    feed: Feed


def _clone_input(source: Path, target: Path) -> None:
    from rebuild.review import build

    if not source.exists():
        raise SystemExit(f"the corpus step reads {source}, which does not exist; build the live corpus first")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not build._clone(source, target):
        raise SystemExit(
            f"the volume will not clone {source} to {target}, and a copy would write it, so the corpus step stops"
        )


def clone_inputs(root: Path, base: Path) -> CorpusInputs:
    """Clone every input a corpus line's build reads from the checkout at `root` into `base` (`CorpusInputs.under`) and return where they are. Each clone is `build._clone`'s, which writes no data and refuses where the volume cannot clone."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import build, subset_pack, unit_cache

    m1 = root / build.M1_SUBSETS.relative_to(build.REPO_ROOT)
    review = root / build.DEFAULT_OUT.relative_to(build.REPO_ROOT)
    inputs = CorpusInputs.under(base)
    pairs = [
        *((table, inputs.m1 / table.name) for table in sorted(m1.glob("baseline-*.subset.tsv.gz"))),
        (m1 / build.M1_AUDIT.name, inputs.audit),
        (m1 / subset_pack.PACK_NAME, inputs.subset_pack),
        (m1 / fingerprint.STAGE_A_FILENAME, inputs.m1 / fingerprint.STAGE_A_FILENAME),
        (m1 / build.M1_AFTER_FONT.name, inputs.after_font),
        (review / "manifest.json", inputs.manifest),
        (unit_cache.signature_store_path(review), inputs.signatures),
        *((root / relative, inputs.spec_root / relative) for relative in SPEC_PATHS),
        (root / build.SITE_BEFORE_FONT.relative_to(build.REPO_ROOT), inputs.before_font),
        (root / build.SITE_JUNIOR_FONT.relative_to(build.REPO_ROOT), inputs.junior_font),
    ]
    for source, target in pairs:
        _clone_input(source, target)
    return inputs


def check_inputs(inputs: CorpusInputs) -> dict[str, object]:
    """Refuse inputs a corpus line could not build over without shaping or writing: a live corpus whose manifest's Stage A values are not the M1 outputs' record beside the tables, since its signature store would then miss, and a pack that is not current for the tables, since the build would rewrite it. Return the inputs' identity a line records: that Stage A record and the live corpus's `generated_at`."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import audit, subset_pack

    manifest = json.loads(inputs.manifest.read_text(encoding="utf-8"))
    recorded = manifest.get("inputs_fingerprint") or {}
    stage_a = fingerprint.read_stage_a(inputs.m1)
    if stage_a is None or any(recorded.get(key) != stage_a[key] for key in fingerprint.STAGE_A_COMPONENTS):
        raise SystemExit(
            f"the corpus step refuses: the live corpus's manifest ({inputs.manifest}) was not built over the M1 outputs' "
            f"Stage A record ({inputs.m1 / fingerprint.STAGE_A_FILENAME}), so its signature store would miss; run a cycle pass first"
        )
    digests = subset_pack.table_digests(inputs.m1, audit.ACCEPTANCE_CONFIGS)
    try:
        header, _base = subset_pack._read_header(inputs.subset_pack)
    except OSError, ValueError:
        header = None
    if (
        header is None
        or header.get("configs") != list(audit.ACCEPTANCE_CONFIGS)
        or subset_pack._header_disagreement(header, digests) is not None
    ):
        raise SystemExit(
            f"the corpus step refuses: {inputs.subset_pack} is not current for the subset tables beside it, so the build "
            "would rewrite it; run a cycle pass first"
        )
    return {
        "stage_a": {key: stage_a[key] for key in fingerprint.STAGE_A_COMPONENTS},
        "corpus_generated_at": manifest.get("generated_at"),
    }


def corpus_cuts(spec: ResolvedSpec, sizes: Sequence[int], scales: Sequence[int]) -> list[CorpusCut]:
    """The corpus lines to measure, in order: a cut line at each of `sizes` (`scaling_series.sub_spec` over `scaling_series.series_order`), then a load line at each of `scales` over the whole alphabet."""
    order = scaling_series.series_order(spec)
    every = {rune.codepoint for rune in spec.runes.values() if rune.codepoint is not None}
    cuts: list[CorpusCut] = []
    for size in sizes:
        cut = scaling_series.sub_spec(spec, order, size)
        kept = {rune.codepoint for rune in cut.runes.values() if rune.codepoint is not None}
        cuts.append(
            CorpusCut(f"r{len(cut.runes)}", tuple(sorted(cut.runes)), len(kept), frozenset(every - kept))
        )
    whole = tuple(sorted(spec.runes))
    for scale in scales:
        cuts.append(CorpusCut(f"{LOAD_PREFIX}{scale}", whole, len(every), frozenset(), scale, True))
    return cuts


def corpus_widths(
    jobs: int | None = None, signature_jobs: int | None = None, write_jobs: int | None = None
) -> dict[str, int]:
    """The widths every corpus line runs at: the build's own command-line defaults on this machine (`artifact_cycle.corpus_job_budget(skip_gates=True)`, `signature_job_budget()`, and `corpus_write_job_budget` of the units width, as `build.main` and `build_m1` take them), each replaced by the one stated."""
    units = jobs or artifact_cycle.corpus_job_budget(skip_gates=True)
    return {
        "jobs": max(1, units),
        "signature_jobs": max(1, signature_jobs or artifact_cycle.signature_job_budget()),
        "write_jobs": max(1, write_jobs or artifact_cycle.corpus_write_job_budget(units)),
    }


def corpus_environment(environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """A corpus or replay child's environment: this process's, without the debug tally's switch, which a run refuses anyway, and the cycle's run id, neither of which a measured build should see."""
    from rebuild.tools import cycle_timings

    return {
        key: value
        for key, value in environ.items()
        if key not in (memory_tally.TALLY_ENV, cycle_timings.CYCLE_RUN_ENV)
    }


def corpus_code(checkout: Path) -> dict[str, str]:
    """The digests of what a corpus line's units workers run from the checkout at `checkout`: the unit store's code stamp over `unit_cache.corpus_code_paths`, and the crate binary the enricher calls."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import unit_cache

    return {
        "corpus_code": fingerprint.digest_lines(
            fingerprint.path_lines(checkout, unit_cache.corpus_code_paths(checkout))
        ),
        "kernel": fingerprint.file_sha256(kernel_exec.BINARY),
    }


def null_routes(manifest: Mapping, class_ids: Iterable[str]) -> list[str]:
    """The paths under a cut line's out dir to link to the null device: a staging name for every output the build has, so a cut line writes none of them for real. Each class the live manifest lists gets a shard part's staging name for its live part count plus one, counted from the part names the manifest lists (`unit_index.class_shards`), since a cut's class holds a subset of the live class's units, and every other id in `class_ids` (the ledger's and `audit.RESERVED_CLASS_IDS`) gets one; each class also gets its first overflow part's staging name. Then the units index, the three app sidecars and the signature store, each under its writer's `.partial` name, and the unit store's head, its generation-0 base and that base's first segment (`row_delta`). A cut line's build finds no previous corpus, so it writes only the base parts, the head and the generation-0 base of these; the overflow part and the segment are routed all the same."""
    from rebuild.pipeline import row_delta
    from rebuild.review import app_index, unit_cache, unit_index

    parts = {meta["id"]: len(unit_index.class_shards(meta)) for meta in manifest["classes"]}
    for class_id in class_ids:
        parts.setdefault(class_id, 0)
    routes = [
        f"units/{class_id}.{index:03d}.json.partial"
        for class_id, count in sorted(parts.items())
        for index in range(count + 1)
    ] + [f"{unit_index.overflow_part_name(class_id, 0)}.partial" for class_id in sorted(parts)]
    sidecars = (
        unit_index.INDEX_NAME,
        app_index.APP_INDEX_NAME,
        app_index.LOCATOR_NAME,
        app_index.LOCATOR_ROWS_NAME,
        unit_cache.SIGNATURE_STORE_NAME,
        row_delta.head_name(unit_cache.STORE_NAME),
        row_delta.base_name(unit_cache.STORE_NAME, 0),
        row_delta.segment_name(unit_cache.STORE_NAME, 0, 1),
    )
    return routes + [f"{name}.partial" for name in sidecars]


def expected_files(load: bool) -> frozenset[str]:
    """The regular files a line may leave in its directory: the build's pool records, and on a cut line the outputs the build writes for real (the manifest, both fonts, `review-facts.json` and the static app files)."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import build, facts

    if load:
        return frozenset({POOLS_NAME})
    static = [
        path.relative_to(build.STATIC_DIR).as_posix() for path in fingerprint.static_files(build.STATIC_DIR)
    ]
    names = (
        "manifest.json",
        "fonts/before.otf",
        "fonts/after.otf",
        facts.FACTS_FILENAME,
        "index.html",
        *static,
    )
    return frozenset({POOLS_NAME, *(f"{OUT_NAME}/{name}" for name in names)})


def step_files(step_dir: Path, expected: frozenset[str]) -> dict[str, dict[str, int]]:
    """Every regular file under `step_dir` with its size, by its path under it: under `kept` when `expected` names it, and under `stray` otherwise. Links and the FIFO are not regular files."""
    found: dict[str, dict[str, int]] = {"kept": {}, "stray": {}}
    for path in sorted(step_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        name = path.relative_to(step_dir).as_posix()
        found["kept" if name in expected else "stray"][name] = path.stat().st_size
    return found


def route_corpus(step_dir: Path, signatures: Path, routes: Sequence[str] | None) -> Path:
    """Make `step_dir`, which must not exist, with the audit's FIFO, the build's out dir and the temporary directory its `TMPDIR` names (`TMP_NAME`), and return the FIFO. With `routes`, for a cut line, the out dir also holds a clone of the live signature store `signatures`, which the build's load reads, and a link to the null device at each of `routes`. A load line's out dir stays empty, since its build stops before it writes anything."""
    step_dir.mkdir(parents=True)
    fifo = step_dir / AUDIT_NAME
    os.mkfifo(fifo)
    out_dir = step_dir / OUT_NAME
    out_dir.mkdir()
    (step_dir / TMP_NAME).mkdir()
    if routes is not None:
        _clone_input(signatures, out_dir / signatures.name)
        for relative in routes:
            link = out_dir / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(os.devnull)
    return fifo


def cut_pattern(excluded: Iterable[int]) -> re.Pattern[bytes] | None:
    """Return the pattern that matches each whole audit line, newline included, whose window holds none of `excluded`'s code points, or None when nothing is excluded and every line is kept. The window is the line's second field, four or more uppercase hex digits a code point joined by colons (`audit.format_codepoints`), so a code point is matched as a whole token of it."""
    tokens = sorted(f"{value:04X}".encode() for value in excluded)
    if not tokens:
        return None
    return re.compile(
        rb"^(?![^\t\n]*\t[^\t\n]*?(?<![0-9A-F])(?:" + b"|".join(tokens) + rb")(?![0-9A-F]))[^\n]*\n",
        re.MULTILINE,
    )


def moved_row(line: bytes) -> bytes:
    """Return an audit line's copy for the ×2 load line: each Quikscript code point of its window moved up by `MOVED` and both rendered-name fields tagged `.x`, so the copy is a new window with new names under the same configuration, kinds and class, as #618's corpus load probe made it at `--scale 2`."""
    config, codepoints, kinds, entry, baseline, new = line.rstrip(b"\n").split(b"\t")
    moved = b":".join(
        b"%04X" % (int(part, 16) + MOVED) if part.startswith(b"E6") else part
        for part in codepoints.split(b":")
    )
    return b"\t".join((config, moved, kinds, entry, baseline + b".x", new + b".x")) + b"\n"


def feed_audit(source: Path, sink: BinaryIO, pattern: re.Pattern[bytes] | None, scale: int) -> int:
    """Write `source`'s header and each line `pattern` keeps (every line when it is None) to `sink`, then at a `scale` of 2 each kept line's moved copy (`moved_row`), and return how many rows were written. The file is read `READ_CHUNK_BYTES` at a time and each chunk is cut at its last newline, so the pattern runs over whole lines."""
    rows = 0
    for copy in range(scale):
        with source.open("rb") as reader:
            header = reader.readline()
            if not copy:
                sink.write(header)
            remainder = b""
            while True:
                chunk = reader.read(READ_CHUNK_BYTES)
                data = remainder + chunk
                if not chunk:
                    block, remainder = (data + b"\n" if data else data), b""
                else:
                    cut = data.rfind(b"\n") + 1
                    block, remainder = data[:cut], data[cut:]
                kept = block if pattern is None else b"".join(pattern.findall(block))
                rows += kept.count(b"\n")
                if copy:
                    kept = b"".join(map(moved_row, kept.splitlines(keepends=True)))
                sink.write(kept)
                if not chunk:
                    break
    return rows


def _running(pid: int) -> bool:
    return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None


def _open_fifo(fifo: Path, alive: Callable[[], bool]) -> int | None:
    """Open `fifo` for writing once its reader has opened it, or return None once `alive` says the reader exited first. A nonblocking open fails with ENXIO until a reader exists, so the writer never blocks on a reader that will not come; the descriptor blocks once it is open."""
    while True:
        try:
            descriptor = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno != errno.ENXIO:
                raise
            if not alive():
                return None
            time.sleep(FIFO_POLL_S)
            continue
        os.set_blocking(descriptor, True)
        return descriptor


def _feed(
    fifo: Path,
    source: Path,
    pattern: re.Pattern[bytes] | None,
    scale: int,
    alive: Callable[[], bool],
    fed: list[Feed],
    origin: float,
) -> None:
    try:
        descriptor = _open_fifo(fifo, alive)
        if descriptor is None:
            fed.append(Feed(0, "the build exited before it opened the audit"))
            return
        with os.fdopen(descriptor, "wb", buffering=READ_CHUNK_BYTES) as sink:
            rows = feed_audit(source, sink, pattern, scale)
        fed.append(Feed(rows, closed_s=time.perf_counter() - origin))
    except OSError as error:
        fed.append(Feed(0, f"{type(error).__name__}: {error}"))


@functools.cache
def _proc_listchildpids() -> Callable[..., int] | None:
    if sys.platform != "darwin":
        return None
    call = ctypes.CDLL(None, use_errno=True).proc_listchildpids
    call.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    call.restype = ctypes.c_int
    return call


def child_pids(pid: int) -> list[int]:
    """The pids whose parent is `pid`: libproc's `proc_listchildpids` on Darwin, `/proc`'s children list elsewhere, and none where neither answers."""
    call = _proc_listchildpids()
    if call is None:
        try:
            return [int(word) for word in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
        except OSError:
            return []
    buffer = (ctypes.c_int * CHILD_PIDS_MAX)()
    count = call(pid, buffer, ctypes.sizeof(buffer))
    return [value for value in buffer[: max(0, count)] if value > 0]


def descendants(pid: int) -> list[tuple[int, int]]:
    """Every process below `pid`, each with its depth, its children's children included."""
    found: list[tuple[int, int]] = []
    frontier = [(pid, 0)]
    while frontier:
        parent, depth = frontier.pop()
        for child in child_pids(parent):
            found.append((child, depth + 1))
            frontier.append((child, depth + 1))
    return found


def _command(pid: int) -> str:
    done = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return done.stdout.strip()


class _Descendants:
    """The processes below one child, as the sampler's `tick` reads them: each one's highest resident set and footprint, and the command of each of the child's own children, so a pool's worker can be told from the child's other children. A child's command is read again at each scan until it names multiprocessing, since a process caught between its fork and its exec still shows its parent's."""

    def __init__(self, pid: int) -> None:
        self._pid = pid
        self.seen: dict[int, Descendant] = {}

    def scan(self, at: float) -> None:
        for pid, depth in descendants(self._pid):
            found = self.seen.get(pid)
            if found is None:
                found = self.seen[pid] = Descendant(pid, depth, at, "")
            if depth == 1 and "multiprocessing" not in found.command:
                found.command = _command(pid) or found.command
            found.read(peak_rss.resident_bytes(pid), peak_rss.footprint_bytes(pid))


def _drain_lines(lines: list[tuple[float, str]], origin: float, stream: IO[bytes]) -> None:
    with stream:
        for raw in stream:
            text = raw.decode(errors="replace").rstrip("\n")
            lines.append((time.perf_counter() - origin, text))
            if text.startswith(WARN):
                print(text, file=sys.stderr, flush=True)


def run_corpus_child(
    arguments: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    audit: Path,
    fifo: Path,
    pattern: re.Pattern[bytes] | None,
    scale: int,
    interval: float,
    parent_interval: float = PARENT_INTERVAL_S,
    machine: Callable[[], Machine] = read_machine,
) -> CorpusRun:
    """Run one corpus child as `run_child` runs a kernel child, in a session of its own, with a thread feeding `fifo` from `audit` (`feed_audit`), the child's stderr read line by line with each line's arrival time, a sampler of the child alone every `parent_interval` seconds, and the `interval` sampler, which reads the machine, also reading every process below the child (`_Descendants`). Every `[warn]` line the child prints is echoed to this process's stderr as it arrives. Once both pipes close and the child has exited, unreaped, the feed is joined, the samplers stop, the child's peak footprint is read, and it is reaped. An exception while it runs, an interrupt or the stop signal `artifact_cycle.stop_signals` raises among them, kills the child's whole process group, its pool workers with it, and reaps the child before it propagates, since a worker would otherwise outlive the line and hold its memory beside the next one."""
    begin = machine()
    origin = time.perf_counter()
    process = subprocess.Popen(
        list(arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    drained: dict[str, bytes] = {}
    lines: list[tuple[float, str]] = []
    fed: list[Feed] = []
    readers = [
        threading.Thread(target=_drain, args=(drained, "stdout", process.stdout), daemon=True),
        threading.Thread(target=_drain_lines, args=(lines, origin, process.stderr), daemon=True),
    ]
    feeder = threading.Thread(
        target=_feed,
        args=(fifo, audit, pattern, scale, lambda: _running(process.pid), fed, origin),
        daemon=True,
    )
    found = _Descendants(process.pid)
    trace = Sampler(process.pid, parent_interval, lambda: QUIET, origin)
    sampler = Sampler(process.pid, interval, machine, origin, tick=found.scan)
    try:
        for thread in (*readers, feeder):
            thread.start()
        trace.start()
        sampler.start()
        for reader in readers:
            reader.join()
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        wall = time.perf_counter() - origin
        feeder.join()
        samples = sampler.stop()
        traced = trace.stop()
        footprint = peak_rss.peak_footprint_bytes(process.pid)
        _pid, status, usage = os.wait4(process.pid, 0)
    except BaseException:
        sampler.stop()
        trace.stop()
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
    process.returncode = os.waitstatus_to_exitcode(status)
    child = ChildRun(
        process.returncode,
        drained["stdout"],
        "".join(f"{text}\n" for _at, text in lines),
        wall,
        usage.ru_utime + usage.ru_stime,
        peak_rss.maxrss_to_bytes(usage.ru_maxrss),
        footprint,
        begin,
        samples,
        machine(),
    )
    return CorpusRun(
        child, tuple(lines), traced, tuple(found.seen.values()), fed[0] if fed else Feed(0, "no feed")
    )


def corpus_child_arguments(
    inputs: CorpusInputs, step_dir: Path, cut: CorpusCut, widths: Mapping[str, int]
) -> list[str]:
    """The command line of one corpus line's child (`memory_series_child`)."""
    return [
        sys.executable,
        "-m",
        memory_series_child.__name__,
        "--inputs",
        inputs.to_json(),
        "--audit",
        str(step_dir / AUDIT_NAME),
        "--out",
        str(step_dir / OUT_NAME),
        "--journal",
        str(step_dir / POOLS_NAME),
        f"--jobs={widths['jobs']}",
        f"--signature-jobs={widths['signature_jobs']}",
        f"--write-jobs={widths['write_jobs']}",
        *(["--load"] if cut.load else []),
    ]


def _count(text: str) -> int:
    return int(text.replace(",", ""))


def _median(values: Sequence[int]) -> int | None:
    return round(statistics.median(values)) if values else None


def corpus_marks(lines: Sequence[tuple[float, str]], trace: Sequence[Sample]) -> list[dict[str, Any]]:
    """Return the child's `[m]` lines in order, each with its label, the seconds it arrived at, the resident set and footprint the child read of itself at the mark, the highest of each the trace read since the previous mark's arrival (the mark's own included), and its counts."""
    marks: list[dict[str, Any]] = []
    since = float("-inf")
    for at, text in lines:
        match = _MARK_LINE.match(text)
        if match is None:
            continue
        values = {
            key: None if value == "-" else int(value)
            for key, _, value in (token.partition("=") for token in match.group(2).split())
        }
        resident = values.pop("resident", None)
        footprint = values.pop("footprint", None)
        window = [sample for sample in trace if since < sample.at <= at]
        marks.append(
            {
                "label": match.group(1),
                "at_s": round(at, 3),
                "resident": resident,
                "footprint": footprint,
                "resident_max": _highest([resident, *(sample.resident for sample in window)]),
                "footprint_max": _highest([footprint, *(sample.footprint for sample in window)]),
                **values,
            }
        )
        since = at
    return marks


def corpus_phases(stderr: str) -> dict[str, dict[str, Any]]:
    """Return each `[t] review.build <phase>` line by phase: its seconds, its `rss_gb` and `rss_now_gb` tokens, and the note after its tab."""
    phases: dict[str, dict[str, Any]] = {}
    for match in INNER_LINE.finditer(stderr):
        label = match.group(1)
        if not label.startswith("review.build "):
            continue
        tokens, _, note = (match.group(3) or "").partition("\t")
        entry: dict[str, Any] = {"seconds": float(match.group(2))}
        for token in tokens.split():
            key, separator, value = token.partition("=")
            if separator and key in ("rss_gb", "rss_now_gb"):
                entry[key] = float(value)
        entry["note"] = note
        phases[label.removeprefix("review.build ")] = entry
    return phases


def _rusage(lines: Sequence[tuple[float, str]]) -> dict[str, float]:
    for _at, text in lines:
        if text.startswith(RUSAGE):
            return {
                key: float(value)
                for key, _, value in (token.partition("=") for token in text[len(RUSAGE) :].split())
            }
    return {}


def _pool_reading(record: Mapping | None, members: Sequence[Descendant]) -> dict[str, Any]:
    peaks = sorted(record["worker_peak_rss_bytes"].values()) if record else []
    resident = sorted(member.resident for member in members if member.resident is not None)
    return {
        "width": record["width"] if record else None,
        "reading": record.get("worker_reading", "resident") if record else None,
        "peaks": peaks,
        "peak_median": _median(peaks),
        "peak_top": max(peaks, default=None),
        "controller": record["controller_peak_rss_bytes"] if record else None,
        "resident": resident,
        "resident_median": _median(resident),
        "resident_top": max(resident, default=None),
    }


def corpus_pools(
    records: Sequence[Mapping], found: Sequence[Descendant], marks: Sequence[Mapping]
) -> dict[str, dict[str, Any]]:
    """Return each pool's reading (`POOL_MARKS`): its last pool record in the line's journal, and the workers (`Descendant.worker`) the sampler first saw after the previous pool's mark and by its own."""
    at = {mark["label"]: mark["at_s"] for mark in marks}
    workers = [member for member in found if member.worker]
    pools: dict[str, dict[str, Any]] = {}
    since = float("-inf")
    for name, unit, closing in POOL_MARKS:
        record = next((record for record in reversed(records) if record.get("unit") == unit), None)
        end = at.get(closing)
        members = [] if end is None else [member for member in workers if since < member.first_at <= end]
        if end is not None:
            since = end
        pools[name] = _pool_reading(record, members)
    return pools


def subprocesses(found: Sequence[Descendant]) -> dict[str, int | None]:
    """Return how many processes below the parent were neither a pool's worker nor its resource tracker, the git and crate calls the parent and its workers make, with the highest resident set and footprint any of them reached."""
    others = [member for member in found if not member.worker and not member.tracker]
    return {
        "count": len(others),
        "resident_top": _highest(member.resident for member in others),
        "footprint_top": _highest(member.footprint for member in others),
    }


def write_runs(manifest: Mapping, write_jobs: int) -> list[dict[str, Any]]:
    """Return the write runs a corpus build with `manifest` wrote at `write_jobs`: its classes in shard order, cut by `build._write_runs` at `build._write_width`, as `build._write_corpus` cuts them, each with its classes and units."""
    from rebuild.review import build, unit_index

    to_write = [
        build._ClassToWrite(
            meta["id"], "", "", "", "", "", False, False, "", array("I", bytes(4 * meta["unit_count"])), ()
        )
        for meta in sorted(manifest["classes"], key=lambda meta: unit_index.class_shard_key(meta["id"]))
    ]
    runs = build._write_runs(to_write, build._write_width(to_write, write_jobs))
    return [
        {"classes": [entry.id for entry in run], "units": sum(len(entry.ordinals) for entry in run)}
        for run in runs
    ]


def check_corpus_child(run: CorpusRun, cut: CorpusCut, marks: Sequence[Mapping]) -> None:
    """Fail the run on a child that exited nonzero, a feed that stopped early, or a load line whose build did not stop at the plan's mark, naming the last lines the child printed."""
    tail = "\n".join(text for _at, text in run.lines[-20:])
    if run.child.returncode != 0:
        raise SystemExit(f"{cut.name}: the corpus build exited {run.child.returncode}:\n{tail}")
    if run.feed.error is not None:
        raise SystemExit(f"{cut.name}: the audit's feed stopped early ({run.feed.error}):\n{tail}")
    if cut.load and (not marks or marks[-1]["label"] != PLAN_MARK):
        raise SystemExit(f"{cut.name}: the load line's build did not stop at the plan:\n{tail}")


def measure_corpus_line(
    inputs: CorpusInputs,
    step_dir: Path,
    cut: CorpusCut,
    widths: Mapping[str, int],
    routes: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    idle_s: float = IDLE_S,
    after_s: float = AFTER_S,
    interval_s: float = INTERVAL_S,
    meter: Callable[[], write_meter.WriteReading] = write_meter.read,
    machine: Callable[[], Machine] = read_machine,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run one corpus line in `step_dir`, which must not exist and is removed once the line's files are listed, and return its measured fields, everything a corpus line holds but the cut, the widths and the provenance (the module docstring lists them). The meter windows are the table step's: an idle reading `idle_s` before the one just before the spawn, which every block reads from, `end` once the child is reaped, and `after` `after_s` later; the step directory is made before the idle reading and removed after `end`, so `end` holds neither. The child's `TMPDIR` is the step directory's `temp/`, so the temporary files the build's processes make stay inside the step, and one a process leaves there is a stray. The checkout's code digests (`corpus_code` at `cwd`) are read after the idle wait, just before the spawn, and once the child is reaped, before the `after` wait, and returned as `code` and, when they moved, `code_after`. `meter`, `machine` and `sleep` are the write meter, the machine reading and the wait, which a test replaces."""
    from rebuild.review import build

    out_dir = step_dir / OUT_NAME
    arguments = corpus_child_arguments(inputs, step_dir, cut, widths)
    try:
        fifo = route_corpus(step_dir, inputs.signatures, None if cut.load else routes)
        idle = None
        if idle_s > 0:
            idle = meter()
            sleep(idle_s)
        code = corpus_code(cwd)
        start = meter()
        run = run_corpus_child(
            arguments,
            env={**env, "TMPDIR": str(step_dir / TMP_NAME)},
            cwd=cwd,
            audit=inputs.audit,
            fifo=fifo,
            pattern=cut_pattern(cut.excluded),
            scale=cut.scale,
            interval=interval_s,
            machine=machine,
        )
        end = meter()
        code_after = corpus_code(cwd)
        pools_path = step_dir / POOLS_NAME
        records = (
            [json.loads(text) for text in pools_path.read_text(encoding="utf-8").splitlines() if text]
            if pools_path.is_file()
            else []
        )
        manifest_path = out_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
        files = step_files(step_dir, expected_files(cut.load))
    finally:
        shutil.rmtree(step_dir, ignore_errors=True)
    rate = None if idle is None else idle_rate(idle, start)
    marks = corpus_marks(run.lines, run.trace)
    check_corpus_child(run, cut, marks)
    after: dict[str, object] | None = None
    if after_s > 0:
        sleep(after_s)
        later = meter()
        after = write_fields(start, later, rate, _difference(run.child.begin.swapouts, machine().swapouts))
    child = run.child
    phases = corpus_phases(child.stderr)
    by_label = {mark["label"]: mark for mark in marks}
    load_note = phases.get("load", {}).get("note", "")
    units_note = phases.get("units", {}).get("note", "")
    write_note = phases.get("manifest+check", {}).get("note", "")
    shaped = _SIGNATURE_NOTE.search(load_note)
    recomputed = _RECOMPUTED_NOTE.search(units_note)
    units_jobs = _JOBS_NOTE.search(units_note)
    write_jobs = _JOBS_NOTE.search(write_note)
    arena = _ARENA_NOTE.search(units_note)
    sidecars = _SIDECAR_NOTE.search(write_note)
    recomputed_count = _count(recomputed.group(1)) if recomputed else None
    rusage = _rusage(run.lines)
    return {
        "rows": run.feed.rows,
        "feed_s": None if run.feed.closed_s is None else round(run.feed.closed_s, 3),
        "units_premerge": by_label.get("load_workload done", {}).get("units"),
        "units": by_label.get(LOAD_MARK, {}).get("units"),
        "windows": by_label.get("by_codepoints built", {}).get("windows"),
        "human_units": None if manifest is None else len(manifest["human_unit_ids"]),
        "recomputed": recomputed_count,
        "classes": None if manifest is None else len(manifest["classes"]),
        "write_runs": None if manifest is None else write_runs(manifest, widths["write_jobs"]),
        "units_width": int(units_jobs.group(1)) if units_jobs else None,
        "handout": (
            build._handout_width(recomputed_count, min(widths["jobs"], recomputed_count))
            if recomputed_count
            else None
        ),
        "write_width": int(write_jobs.group(1)) if write_jobs else None,
        "allocator_env": {key: value for key, value in env.items() if key.startswith("Malloc")},
        "wall_s": round(child.wall, 3),
        "cpu_s": round(rusage["cpu_s"], 3) if "cpu_s" in rusage else None,
        "peak_resident": int(rusage["peak_resident"]) if "peak_resident" in rusage else None,
        "peak_footprint": child.peak_footprint,
        "children": {
            "cpu_s": round(rusage["children_cpu_s"], 3) if "children_cpu_s" in rusage else None,
            "peak_resident": (
                int(rusage["children_peak_resident"]) if "children_peak_resident" in rusage else None
            ),
        },
        "samples": len(child.samples),
        "interval_s": interval_s,
        "parent_samples": len(run.trace),
        "parent_interval_s": PARENT_INTERVAL_S,
        **moments(child),
        "marks": marks,
        "phases": phases,
        "signatures_shaped": _count(shaped.group(2)) if shaped else None,
        "pools": corpus_pools(records, run.descendants, marks),
        "subprocesses": subprocesses(run.descendants),
        "fragments": {
            "recomputed": recomputed_count,
            "arena_gb": float(arena.group(1)) if arena else None,
            "deflated": float(arena.group(2)) if arena else None,
        },
        "sidecar_lines_mb": [float(size) for size in sidecars.group(1).split(", ")] if sidecars else None,
        "written": {
            "idle_s": idle_s,
            "idle_rate": None if rate is None else round(rate),
            "end": write_fields(start, end, rate, _difference(child.begin.swapouts, child.end.swapouts)),
            "after_s": after_s,
            "after": after,
        },
        "files": files,
        "code": code,
        **({"code_after": code_after} if code_after != code else {}),
    }


def _mark_value(line: Mapping, label: str, key: str) -> int | None:
    return next((mark.get(key) for mark in line["marks"] if mark["label"] == label), None)


def corpus_report(lines: Sequence[Mapping]) -> list[str]:
    """Return the fit over the cut lines of each corpus figure against units (`scaling_sweep.fit`): the parent's peak footprint, resident peak and CPU time, the top units and write workers' peaks, the arena and the write runs' sidecar lines, and of the parent's footprint at the load's and the plan's marks against rows; then the ×2 load line's ratio to the ×1 line in its peak footprint and its footprint at each mark both reached, in `CORPUS_MARKS` order. A figure a line lacks is left out of its fit."""
    cuts = [line for line in lines if line["step"] == CORPUS_STEP]
    loads = {line["scale"]: line for line in lines if line["step"] == LOAD_STEP}
    text: list[str] = []
    if len(cuts) < 2:
        text.append(f"the corpus fit needs two cuts; this run has {len(cuts)}")
    else:
        units = [line["units"] or 0 for line in cuts]
        rows = [line["rows"] for line in cuts]
        by_units: dict[str, list[float | None]] = {
            "peak footprint": [line["peak_footprint"] for line in cuts],
            "peak resident": [line["peak_resident"] for line in cuts],
            "cpu": [line["cpu_s"] for line in cuts],
            "units worker top": [line["pools"]["units"]["peak_top"] for line in cuts],
            "write worker top": [line["pools"]["write"]["peak_top"] for line in cuts],
            "arena": [line["fragments"]["arena_gb"] for line in cuts],
            "sidecar lines": [sum(line["sidecar_lines_mb"] or ()) or None for line in cuts],
        }
        by_rows: dict[str, list[float | None]] = {
            f"{label} footprint": [_mark_value(line, label, "footprint") for line in cuts]
            for label in (LOAD_MARK, PLAN_MARK)
        }
        width = max(len(label) for label in (*by_units, *by_rows))
        text.append(f"corpus fit over {len(cuts)} cuts (units {min(units)}..{max(units)})")
        text.extend(
            f"  {label:<{width}} ~ units^{scaling_sweep.exponent(scaling_sweep.fit(units, values))}"
            for label, values in by_units.items()
        )
        text.extend(
            f"  {label:<{width}} ~ rows^{scaling_sweep.exponent(scaling_sweep.fit(rows, values))}"
            for label, values in by_rows.items()
        )
    if 1 in loads and 2 in loads:
        one, two = loads[1], loads[2]
        ratios = [("peak footprint", one["peak_footprint"], two["peak_footprint"])]
        ratios += [
            (f"{label} footprint", _mark_value(one, label, "footprint"), _mark_value(two, label, "footprint"))
            for label in CORPUS_MARKS
        ]
        shown = [(label, a, b) for label, a, b in ratios if a and b]
        width = max((len(label) for label, _a, _b in shown), default=0)
        text.append(f"load-x2 over load-x1 ({two['rows']} rows over {one['rows']})")
        text.extend(f"  {label:<{width}} {b / a:.2f}" for label, a, b in shown)
    return text


def build_replay_tables(
    spec_path: Path,
    cut_dir: Path,
    configs: Sequence[str],
    flags: Flags,
    stamp: str,
    *,
    env: Mapping[str, str] | None,
) -> None:
    """Build one cut's tables, unmeasured, for the replay holder's lines to walk: one `build-tables` at the flags the table holder's step takes, its settlement and joins tables written plain into `cut_dir`, which must not exist, and each windows payload drained from a FIFO at its path (`kernel_exec.stream_payloads`), so no payload reaches the disk. A build that fails stops the run (`check_child`)."""
    cut_dir.mkdir(parents=True)
    arguments = [
        str(kernel_exec.BINARY),
        "build-tables",
        str(spec_path),
        str(cut_dir),
        f"--configs={','.join(configs)}",
        f"--inputs={stamp}",
        f"--threads={flags.threads}",
        *kernel_exec.mode_flags(),
    ]
    if flags.scratch_beside_default:
        arguments.append(f"--scratch-beside-default={flags.scratch_beside_default}")
    paths = {f"{WINDOWS} {config}": kernel_exec.windows_payload(cut_dir, config) for config in configs}
    child, _drained = kernel_exec.stream_payloads(
        paths,
        lambda _name, source: _discard(source),
        lambda: run_child(arguments, env=env, interval=INTERVAL_S, machine=lambda: QUIET),
    )
    check_child(child, configs, arguments)


def _discard(source: IO[bytes]) -> None:
    while source.read(READ_CHUNK_BYTES):
        pass


@dataclass(frozen=True)
class ReplayReading:
    """One reading of a replay line's processes: seconds since the child's spawn and each process's resident set and footprint, by pid."""

    at: float
    readings: dict[int, tuple[int | None, int | None]]


class _ReplayTree:
    """The processes below a replay line's child, as the sampler's `tick` reads them: every process's resident set and footprint at each tick (`ReplayReading`), and each one's command, read again until it names the crate or multiprocessing, since a process caught between its fork and its exec still shows its parent's."""

    def __init__(self, pid: int) -> None:
        self._pid = pid
        self.readings: list[ReplayReading] = []
        self.commands: dict[int, str] = {}

    def scan(self, at: float) -> None:
        seen: dict[int, tuple[int | None, int | None]] = {}
        for pid, _depth in descendants(self._pid):
            command = self.commands.get(pid, "")
            if BINARY_NAME not in command and "multiprocessing" not in command:
                self.commands[pid] = _command(pid) or command
            seen[pid] = (peak_rss.resident_bytes(pid), peak_rss.footprint_bytes(pid))
        self.readings.append(ReplayReading(at, seen))


@dataclass(frozen=True)
class ReplayRun:
    """What `run_replay_child` returns: the child as `run_child` returns one, every reading of the processes below it, their commands, and what the child printed about the replay (`memory_series_child.replay_main`)."""

    child: ChildRun
    readings: tuple[ReplayReading, ...]
    commands: dict[int, str]
    replay: dict[str, Any]


def run_replay_child(
    arguments: Sequence[str],
    *,
    env: Mapping[str, str] | None,
    interval: float,
    machine: Callable[[], Machine] = read_machine,
) -> ReplayRun:
    """Run one replay child as `run_corpus_child` runs a corpus child, in a session of its own, with its stdout and stderr drained and the `interval` sampler, which reads the child and the machine, also reading every process below it (`_ReplayTree`). Once both pipes close and the child has exited, unreaped, the sampler stops, the child's peak footprint is read, and it is reaped. A child that exits nonzero, or prints no replay, stops the run. An exception while it runs kills the child's whole process group, its walks and absorb workers with it, and reaps the child before it propagates."""
    begin = machine()
    origin = time.perf_counter()
    process = subprocess.Popen(
        list(arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    drained: dict[str, bytes] = {}
    readers = [
        threading.Thread(target=_drain, args=(drained, name, stream), daemon=True)
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
    ]
    tree = _ReplayTree(process.pid)
    sampler = Sampler(process.pid, interval, machine, origin, tick=tree.scan)
    try:
        for reader in readers:
            reader.start()
        sampler.start()
        for reader in readers:
            reader.join()
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        wall = time.perf_counter() - origin
        samples = sampler.stop()
        footprint = peak_rss.peak_footprint_bytes(process.pid)
        _pid, status, usage = os.wait4(process.pid, 0)
    except BaseException:
        sampler.stop()
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
    process.returncode = os.waitstatus_to_exitcode(status)
    stderr = drained["stderr"].decode(errors="replace")
    lines = [line for line in drained["stdout"].decode(errors="replace").splitlines() if line.strip()]
    if process.returncode != 0 or not lines or not lines[-1].startswith("{"):
        raise SystemExit(
            f"the replay child exited {process.returncode}: {stderr.strip() or 'it printed no replay'}"
        )
    child = ChildRun(
        process.returncode,
        drained["stdout"],
        stderr,
        wall,
        usage.ru_utime + usage.ru_stime,
        peak_rss.maxrss_to_bytes(usage.ru_maxrss),
        footprint,
        begin,
        samples,
        machine(),
    )
    return ReplayRun(child, tuple(tree.readings), dict(tree.commands), json.loads(lines[-1]))


def replay_child_arguments(
    spec: Path, spec_dump: Path, out: Path, configs: Sequence[str], width: int, *, built: bool
) -> list[str]:
    """The command line of one replay line's child (`memory_series_child.replay_main`). `built` says this process's own `kernel_exec.ensure_built()` has succeeded, which the child then takes as its own, so it runs no `cargo build` inside the line it measures."""
    return [
        sys.executable,
        "-m",
        "rebuild.tools.memory_series_child",
        "replay",
        "--spec",
        str(spec),
        "--spec-dump",
        str(spec_dump),
        "--out",
        str(out),
        "--configs",
        ",".join(configs),
        "--width",
        str(width),
        *(["--built"] if built else []),
    ]


def crate_configs(commands: Mapping[int, str]) -> dict[int, str]:
    """Each walk's crate process, by pid, with the configuration its command line names (`--configs=`)."""
    found: dict[int, str] = {}
    for pid, command in commands.items():
        words = command.split()
        if "replay-strings" not in words or not any(word.endswith(BINARY_NAME) for word in words):
            continue
        for word in words:
            if word.startswith("--configs="):
                found[pid] = word.removeprefix("--configs=")
    return found


def _pair_sums(
    reading: ReplayReading, crates: Mapping[int, str], workers: Mapping[int, str]
) -> dict[str, tuple[int, int]]:
    """Each configuration's walk crate and absorb worker summed in one reading, as resident and footprint, a process missing from the reading or a figure it could not read counting nothing."""
    sums: dict[str, tuple[int, int]] = {}
    for pid, (resident, footprint) in reading.readings.items():
        config = crates.get(pid) or workers.get(pid)
        if config is not None:
            held = sums.get(config, (0, 0))
            sums[config] = (held[0] + (resident or 0), held[1] + (footprint or 0))
    return sums


def _combined(sums: Mapping[str, tuple[int, int]]) -> tuple[int, int]:
    return sum(resident for resident, _ in sums.values()), sum(footprint for _, footprint in sums.values())


def replay_peaks(
    readings: Sequence[ReplayReading], crates: Mapping[int, str], workers: Mapping[int, str]
) -> tuple[dict[str, dict[str, int]], dict[str, int], tuple[float, int, int] | None]:
    """Each configuration's pair peak, its walk's crate and its absorb worker summed at each reading (`_pair_sums`), as the highest resident sum and the highest footprint sum; the step's combined peak, every crate and absorb worker summed the same way; and the reading whose combined footprint, or combined resident set where no footprint was read, is highest, as its time and its combined resident and footprint."""
    pairs = {config: {"resident": 0, "footprint": 0} for config in (*crates.values(), *workers.values())}
    combined = {"resident": 0, "footprint": 0}
    top: tuple[float, int, int] | None = None
    for reading in readings:
        sums = _pair_sums(reading, crates, workers)
        for config, (resident, footprint) in sums.items():
            pairs[config]["resident"] = max(pairs[config]["resident"], resident)
            pairs[config]["footprint"] = max(pairs[config]["footprint"], footprint)
        resident, footprint = _combined(sums)
        combined["resident"] = max(combined["resident"], resident)
        combined["footprint"] = max(combined["footprint"], footprint)
        if top is None or (footprint or resident) > (top[2] or top[1]):
            top = (reading.at, resident, footprint)
    return pairs, combined, top


def replay_cache(lines: Sequence[str], config: str) -> dict[str, Any]:
    """One walk's `[c]` lines as a replay line keeps them (`cache_lines`): the unit count, the largest unit, the one whose walk memo held the most windows when it was released, with every table its release reported and its resident size after the release, and at the walk's end the side memo, the row store and the resident size after the walk."""
    kinds = cache_lines("\n".join(lines))
    tables = kinds["tables"].get(config, {})
    resident = kinds["resident"].get(config, {})
    stats = kinds["stats"].get(config, {})
    released = {
        name: value[0]
        for name, value in tables.items()
        if name.startswith("walk_memo unit=") and isinstance(value, list)
    }
    largest = max(released, key=released.__getitem__, default=None)
    unit = None
    if largest is not None:
        qualifier = largest.removeprefix("walk_memo ")
        unit = {
            "unit": int(qualifier.removeprefix("unit=")),
            "tables": {
                name.removesuffix(f" {qualifier}"): value
                for name, value in tables.items()
                if name.endswith(f" {qualifier}")
            },
            "resident_after_release": resident.get(f"resident_after_release {qualifier}"),
        }
    units = stats.get("units", {})
    return {
        "units": units.get("count") if isinstance(units, dict) else None,
        "largest_unit": unit,
        "side_memo": tables.get("side_memo"),
        "row_store": stats.get("row_store"),
        "resident_after_walk": resident.get("resident_after_walk"),
    }


def measure_replay_line(
    arguments: Sequence[str],
    configs: Sequence[str],
    *,
    idle_s: float = IDLE_S,
    after_s: float = AFTER_S,
    interval_s: float = INTERVAL_S,
    env: Mapping[str, str] | None = None,
    meter: Callable[[], write_meter.WriteReading] = write_meter.read,
    machine: Callable[[], Machine] = read_machine,
    sleep: Callable[[float], None] = time.sleep,
    run: Callable[..., ReplayRun] = run_replay_child,
) -> dict[str, Any]:
    """Run one replay line's child (`run_replay_child`) between the write meter's readings, as `measure_step` runs a table step, and return its measured fields (the module docstring's "The replay step"). A walk that fails stops the run. `meter`, `machine`, `sleep` and `run` are the write meter, the machine reading, the wait and the child, which a test replaces."""
    idle = None
    if idle_s > 0:
        idle = meter()
        sleep(idle_s)
    start = meter()
    ran = run(arguments, env=env, interval=interval_s, machine=machine)
    end = meter()
    rate = None if idle is None else idle_rate(idle, start)
    replay = ran.replay
    walks: Mapping[str, Mapping[str, Any]] = replay["walks"]
    absorbs: Mapping[str, Mapping[str, Any]] = replay["absorbs"]
    failed = [walk["error"] for walk in walks.values() if walk["error"] is not None]
    if failed or not replay["passed"]:
        raise SystemExit(f"the replay failed: {failed[0] if failed else 'not every walk passed'}")
    crates = crate_configs(ran.commands)
    workers = {walk["worker"]: config for config, walk in walks.items() if walk["worker"]}
    pairs, combined, top = replay_peaks(ran.readings, crates, workers)
    child = ran.child
    machines = {sample.at: sample.machine for sample in child.samples}
    readings = [child.begin, *(sample.machine for sample in child.samples), child.end]
    last = _combined(_pair_sums(ran.readings[-1], crates, workers)) if ran.readings else None
    lines: dict[str, Any] = {}
    for config in configs:
        walk = walks[config]
        absorb = absorbs.get(config, {})
        resident, footprint, cpu = walk["peaks"] or (None, None, None)
        digest = absorb.get("digest") or (None, None)
        lines[config] = {
            "counts": walk["counts"],
            "walk": {
                "peak_resident": resident,
                "peak_footprint": footprint,
                "cpu_s": None if cpu is None else round(cpu, 3),
            },
            "absorb": {
                "peak_resident": absorb.get("resident"),
                "peak_footprint": absorb.get("footprint"),
                "cpu_s": None if absorb.get("cpu") is None else round(absorb["cpu"], 3),
                "seconds": None if absorb.get("seconds") is None else round(absorb["seconds"], 3),
                "rows": absorb.get("entries"),
                "bytes": digest[0],
                "sha256": digest[1],
                "warning": absorb.get("warning"),
            },
            "pair": pairs.get(config),
            "phases": {
                match.group(1): float(match.group(2))
                for match in INNER_LINE.finditer("\n".join(walk["lines"]))
            },
            "cache": replay_cache(walk["lines"], config),
        }
    after: dict[str, object] | None = None
    if after_s > 0:
        sleep(after_s)
        later = meter()
        swapouts = _difference(child.begin.swapouts, machine().swapouts)
        after = write_fields(start, later, rate, swapouts)
    return {
        "wall_s": round(child.wall, 3),
        "samples": len(ran.readings),
        "interval_s": interval_s,
        "start": _moment(0.0, None, None, child.begin),
        "peak": None if top is None else _moment(top[0], top[1], top[2], machines.get(top[0], child.end)),
        "end": _moment(
            child.wall, None if last is None else last[0], None if last is None else last[1], child.end
        ),
        "compressor_max": _highest(reading.compressor for reading in readings),
        "swap_used_max": _highest(reading.swap_used for reading in readings),
        "combined": combined,
        "configs_measured": lines,
        "written": {
            "idle_s": idle_s,
            "idle_rate": None if rate is None else round(rate),
            "end": write_fields(start, end, rate, _difference(child.begin.swapouts, child.end.swapouts)),
            "after_s": after_s,
            "after": after,
        },
    }


def replay_report(lines: Sequence[Mapping]) -> list[str]:
    """Return the replay series' fit against runes (`scaling_sweep.fit`), one figure a line: the windows over every configuration, the highest walk peak, absorb peak and pair peak over the configurations (each the higher of its resident and footprint), the step's combined peak, the walks' CPU time over every configuration, the largest row store, and the first configuration's largest unit's walk memo length. A figure a line lacks is left out of its fit."""
    if len({line["runes"] for line in lines}) < 2:
        return [f"the replay fit needs two cuts; this run has {len({line['runes'] for line in lines})}"]
    runes = [line["runes"] for line in lines]

    def most(line: Mapping, part: str) -> int | None:
        values = [
            max(value or 0 for value in (entry[part]["peak_resident"], entry[part]["peak_footprint"]))
            for entry in line["walks"].values()
        ]
        return max(values, default=None) or None

    def pair(line: Mapping) -> int | None:
        return max(
            (max(entry["pair"].values()) for entry in line["walks"].values() if entry["pair"]), default=None
        )

    def unit_memo(line: Mapping) -> int | None:
        unit = next(iter(line["walks"].values()))["cache"]["largest_unit"]
        return None if unit is None else unit["tables"].get("walk_memo", [None])[0]

    figures: dict[str, list[float | None]] = {
        "windows": [sum(entry["counts"]["windows"] for entry in line["walks"].values()) for line in lines],
        "walk peak": [most(line, "walk") for line in lines],
        "absorb peak": [most(line, "absorb") for line in lines],
        "pair peak": [pair(line) for line in lines],
        "combined peak": [max(line["combined"].values()) or None for line in lines],
        "walk cpu": [sum(entry["walk"]["cpu_s"] or 0 for entry in line["walks"].values()) for line in lines],
        "row store bytes": [
            max(
                ((entry["cache"]["row_store"] or {}).get("bytes") or 0 for entry in line["walks"].values()),
                default=0,
            )
            or None
            for line in lines
        ],
        "largest unit walk_memo len": [unit_memo(line) for line in lines],
    }
    width = max(len(label) for label in figures)
    return [
        f"replay fit over {len(lines)} lines (runes {min(runes)}..{max(runes)})",
        *(
            f"  {label:<{width}} ~ runes^{scaling_sweep.exponent(scaling_sweep.fit(runes, values))}"
            for label, values in figures.items()
        ),
    ]


def run_replay(args: argparse.Namespace, tree: Path) -> int:
    """Run the replay holder's lines (the module docstring's "The replay step") under the held lock: build the kernel, empty the tree, write each distinct cut's spec dump and pickle and build its tables once, unmeasured (`build_replay_tables`), then measure each named line in turn, recording each as it finishes, then the replay fit (`replay_report`). The cuts' tables, about 0.05 GB a cut, are removed as the run ends, however it ends."""
    configs = configs_from(args.configs) if args.configs else list(conform.SETTLEMENT_CONFIGS)
    spec = spec_load.load_default_spec()
    order = scaling_series.series_order(spec)
    sizes = args.sizes or default_sizes(len(order), REPLAY_SIZES)
    width = run_m1._replay_threads(args.replay_threads)
    kernel_exec.ensure_built()
    empty_tree(tree)
    tables_env = (
        {**os.environ, **kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT} if sys.platform == "darwin" else None
    )
    env = corpus_environment()
    try:
        cuts: dict[int, tuple[ResolvedSpec, list[str]]] = {}
        flags = bare_flags(configs)
        for size in dict.fromkeys(sizes):
            cut = scaling_series.sub_spec(spec, order, size)
            runes = len(cut.runes)
            dump = tree / f"spec-r{runes}.json"
            pickled = tree / f"spec-r{runes}.pickle"
            kernel_io.write_spec(cut, dump)
            with pickled.open("wb") as handle:
                pickle.dump(cut, handle, protocol=pickle.HIGHEST_PROTOCOL)
            cut_dir = tree / REPLAY / f"r{runes}"
            _progress(f"r{runes}: building the tables its lines walk, unmeasured")
            build_replay_tables(dump, cut_dir, configs, flags, f"{STAMP_PREFIX}{runes}", env=tables_env)
            cuts[size] = (cut, replay_child_arguments(pickled, dump, cut_dir, configs, width, built=True))
        head = _head()
        lines: list[dict[str, Any]] = []
        record = Record(args.out, REPLAY)
        os.sync()
        time.sleep(SETTLE_S)
        for size in sizes:
            cut, arguments = cuts[size]
            runes = len(cut.runes)
            letters = sum(1 for rune in cut.runes.values() if not rune.sequence)
            _progress(
                f"r{runes}: {args.idle_seconds:g} s idle, the replay, then {args.after_seconds:g} s before the meter's last reading"
            )
            measured = measure_replay_line(
                arguments,
                configs,
                idle_s=args.idle_seconds,
                after_s=args.after_seconds,
                interval_s=args.interval,
                env=env,
            )
            _warn_meter(f"r{runes}", measured["written"])
            walks = measured.pop("configs_measured")
            line: dict[str, Any] = {
                "step": REPLAY_STEP,
                "runes": runes,
                "letters": letters,
                "ligs": runes - letters,
                "configs": list(configs),
                "width": width,
                "max_length": run_m1.REPLAY_MAX_LENGTH,
                "modes": " ".join(kernel_exec.settlement_flags()) or "default modes",
                **measured,
                "walks": walks,
                "alphabet": sorted(cut.runes),
                "cpus": memory_budget.usable_cores(),
                "ram": memory_budget.total_memory_bytes(),
                "head": head,
                "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            lines.append(line)
            text = json.dumps(line)
            print(text, flush=True)
            record.add(text)
        for text in ("", *replay_report(lines)):
            print(text, flush=True)
            record.add(text)
        return 0
    finally:
        shutil.rmtree(tree / REPLAY, ignore_errors=True)


def absorb_counts(lines: Sequence[str]) -> list[int | str]:
    """The absorbs holder's row counts from its positional arguments: each a count of rows, or `live` for the live `default` settle memo's own; `ABSORB_COUNTS` when none is named."""
    return [LIVE if text == LIVE else int(text) for text in (lines or ABSORB_COUNTS)]


class _CountRows:
    """The rows of one absorbs count, built from the live `default` settle memo's rows (`conform._MemoStore`, mapped from its clone): row r below the live count is the live row r, and each row past it restates the live row r modulo the live count under labels of its own, every label suffixed with the copy's number, `.r<copy>`, so it names the same family (`conform._label_family`) and its window is one no other row holds. `copy`, when given, restates every row under that copy's labels instead, which gives windows the count's own rows never hold."""

    def __init__(self, store: conform._MemoStore) -> None:
        self.store = store
        self.live = len(store.reached)
        self._tables: dict[int, list[str]] = {0: list(store.labels)}

    def labels(self, copy: int) -> list[str]:
        table = self._tables.get(copy)
        if table is None:
            table = self._tables[copy] = [f"{label}.r{copy}" for label in self.store.labels]
        return table

    def copies(self, count: int) -> int:
        """How many copies past the live rows `count` rows restate."""
        return max(0, -(-count // self.live) - 1)

    def rows(
        self, start: int, stop: int, copy: int | None = None
    ) -> Iterator[tuple[conform._Window, conform._Outcome]]:
        """Rows `start` to `stop` as (window, outcome) pairs, the outcome the store's own object for its value."""
        store = self.store
        at = start
        while at < stop:
            base, own = divmod(at, self.live)
            upto = min(stop, (base + 1) * self.live)
            table = self.labels(base if copy is None else copy)
            first, last = own, own + upto - at
            windows = zip(*(map(table.__getitem__, column[first:last]) for column in store.columns))
            outcomes = map(store.outcomes.__getitem__, store.values[first:last])
            yield from cast(Iterator[tuple[conform._Window, conform._Outcome]], zip(windows, outcomes))
            at = upto

    def columns(self, count: int) -> tuple[list[str], list, list[array], array]:
        """The first `count` rows as the tables and columns `conform._write_settle_memo` takes, built a copy at a time from the mapped columns without a window a row."""
        store = self.store
        copies = self.copies(count)
        labels = [label for copy in range(copies + 1) for label in self.labels(copy)]
        width = len(store.labels)
        columns = [array("I") for _ in range(6)]
        values = array("I")
        for copy in range(copies + 1):
            rows = min(self.live, count - copy * self.live)
            for column, source in zip(columns, store.columns):
                column.extend(map((copy * width).__add__, source[:rows]))
            values.extend(store.values[:rows])
        return labels, [outcome[0] for outcome in store.outcomes], columns, values


@dataclass
class AbsorbCount:
    """One absorbs count's inputs, made once a run: the standing file, its row count, and the fresh windows each path hands its absorb, compressed as a walk hands them (`conform.FreshMemo`): `tail`, the standing file's last `ABSORB_TAIL` rows restating its own windows, and `new`, its last rows under labels the file lacks, as many of the `ABSORB_TAIL` as its index has room for beside its own rows, each as `ABSORB_RANGES` row ranges, and `units`, every row in the `default` sweep's unit count of parts. `written` says the run wrote the standing file, which it removes as it ends. A run none of whose paths reads a standing file makes none: `standing` is None and `tail` and `new` are empty."""

    name: int | str
    rows: int
    standing: Path | None
    written: bool
    tail: list[conform.FreshMemo]
    new: list[conform.FreshMemo]
    units: list[conform.FreshMemo]


def _split(total: int, parts: int) -> list[tuple[int, int]]:
    return [(total * index // parts, total * (index + 1) // parts) for index in range(parts)]


def absorb_count(
    name: int | str,
    source: _CountRows,
    memo: conform.SettleMemoFile,
    clone: Path,
    directory: Path,
    units: int,
    reads_standing: bool,
) -> AbsorbCount:
    """Make one count's inputs (`AbsorbCount`): when `reads_standing` says a path of the run reads a standing file, for `live` the clone of the live memo as that file, and for any other count a file of that many rows (`_CountRows`) written once under the live memo's stamp and keys, and the windows the paths over it take; then every row in the units' parts. Without `reads_standing` it writes no file and makes no tail or new windows."""
    if not reads_standing:
        rows = source.live if name == LIVE else int(name)
        return AbsorbCount(
            name,
            rows,
            None,
            False,
            [],
            [],
            [conform._fresh_memo(source.rows(start, stop)) for start, stop in _split(rows, units)],
        )
    if name == LIVE:
        rows, standing, written = source.live, clone, False
    else:
        rows = int(name)
        standing = directory / f"standing-{rows}.bin"
        if not conform._write_settle_memo(replace(memo, path=standing), *source.columns(rows)):
            raise SystemExit(f"the absorbs holder could not write {standing}")
        written = True
    tail = max(0, rows - ABSORB_TAIL)
    header = conform._settle_memo_header(replace(memo, path=standing))
    if header is None:
        raise SystemExit(f"{standing} is not a settle memo under the live memo's stamp")
    new = max(0, rows - min(ABSORB_TAIL, int(header["slots"]) // 2 - rows))
    restated = source.copies(rows) + 1
    return AbsorbCount(
        name,
        rows,
        standing,
        written,
        [
            conform._fresh_memo(source.rows(tail + start, tail + stop))
            for start, stop in _split(rows - tail, ABSORB_RANGES)
        ],
        [
            conform._fresh_memo(source.rows(new + start, new + stop, copy=restated))
            for start, stop in _split(rows - new, ABSORB_RANGES)
        ],
        [conform._fresh_memo(source.rows(start, stop)) for start, stop in _split(rows, units)],
    )


def absorb_job(
    path: str, count: AbsorbCount, memo: conform.SettleMemoFile, absent: Path
) -> tuple[conform.SettleMemoFile, list[conform.FreshMemo], conform.SweepPrune | None]:
    """One path's absorb over a count's inputs: the file it reads, the fresh windows it is handed, and the sweep's prune, which only the sweep's paths carry. `fast` reads the standing file with the new windows, `duplicate` with the tail's restated ones, and `stale` the same with `ABSORB_STALE_FAMILY`'s key moved; `prune` drops the standing file's last `ABSORB_PRUNED` rows through the sweep's absorb; `fresh` and `fresh-sweep` read no standing file and take every row in the units' parts, through the oracle's absorb and the sweep's."""
    if path in ("fresh", "fresh-sweep"):
        fresh = replace(memo, path=absent)
        if path == "fresh":
            return fresh, count.units, None
        return fresh, count.units, conform.SweepPrune(None, zlib.compress(b"", 1), True, False)
    if count.standing is None:
        raise ValueError(f"the {count.name} count's inputs hold no standing file for the {path} path")
    standing = replace(memo, path=count.standing)
    if path == "fast":
        return standing, count.new, None
    if path == "duplicate":
        return standing, count.tail, None
    if path == "stale":
        keys = {**memo.family_keys, ABSORB_STALE_FAMILY: "moved-for-the-absorbs-holder"}
        return replace(standing, family_keys=keys), count.tail, None
    if path == "prune":
        stat = count.standing.stat()
        kept = max(0, count.rows - ABSORB_PRUNED)
        flags = zlib.compress(b"\x01" * kept + bytes(count.rows - kept), 1)
        identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        return standing, [], conform.SweepPrune(identity, flags, True, False)
    raise ValueError(f"{path} is not an absorbs path")


def _ship_untracked(pieces: Sequence[bytes | memoryview], name: str) -> oracle.Shipped | None:
    """`oracle.ship` without the resource tracker's registration, since the absorb child that maps and unlinks the segment runs under a tracker of its own."""
    from multiprocessing import shared_memory

    lengths = tuple(len(piece) for piece in pieces)
    if not sum(lengths):
        return None
    segment = shared_memory.SharedMemory(name, create=True, size=sum(lengths), track=False)
    buffer = segment.buf
    assert buffer is not None
    try:
        at = 0
        for piece in pieces:
            buffer[at : at + len(piece)] = piece
            at += len(piece)
    finally:
        segment.close()
    return oracle.Shipped(name, lengths)


def _unlink_segments(names: Iterable[str]) -> None:
    for name in names:
        with suppress(OSError, ValueError):
            oracle._unlink_if_made(name, track=False)


class _AbsorbSegments:
    """The names of the shared memory segments an absorbs run ships its lines' parts into, each appended to `record` (`ABSORB_SEGMENTS` under the run's directory) before its segment is made, so that a failure partway through shipping a line, or a SIGKILL, which skips every cleanup, leaves no segment nothing names. `unlink` unlinks every segment named since the last call; the next run's `empty_tree` unlinks every one the record names."""

    def __init__(self, record: Path) -> None:
        self._record = record
        self.names: list[str] = []

    def add(self, name: str) -> str:
        with self._record.open("a", encoding="utf-8") as record:
            record.write(f"{name}\n")
        self.names.append(name)
        return name

    def unlink(self) -> None:
        _unlink_segments(self.names)
        self.names.clear()


def absorb_payload(
    writer: str,
    memo: conform.SettleMemoFile,
    spec: ResolvedSpec,
    parts: Sequence[conform.FreshMemo],
    sweep: conform.SweepPrune | None,
    prefix: str,
    segments: _AbsorbSegments,
) -> bytes:
    """Ship each part's blocks into a shared memory segment of its own, as a range or a unit ships them, naming each in `segments` before making it, so that the caller unlinks every one a child does not, and return the absorb child's job (`memory_series_child.absorb_main`) pickled."""
    shipped = []
    for index, part in enumerate(parts):
        name = segments.add(f"{prefix}-{index}")
        shipped.append((_ship_untracked(list(part.blocks), name), replace(part, blocks=())))
    job = {"writer": writer, "memo": memo, "spec": spec, "parts": shipped, "sweep": sweep}
    return pickle.dumps(job, protocol=pickle.HIGHEST_PROTOCOL)


def run_absorb_child(
    payload: bytes,
    *,
    env: Mapping[str, str] | None,
    interval: float,
    machine: Callable[[], Machine] = read_machine,
) -> tuple[ChildRun, dict[str, Any]]:
    """Run one absorb child (`memory_series_child.absorb_main`) with `payload` on its stdin, sampled as `run_child` samples a crate, and return it with what it printed. A child that exits nonzero, or prints nothing, stops the run."""
    arguments = [sys.executable, "-m", "rebuild.tools.memory_series_child", "absorb"]
    begin = machine()
    origin = time.perf_counter()
    process = subprocess.Popen(
        arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    drained: dict[str, bytes] = {}
    readers = [
        threading.Thread(target=_drain, args=(drained, name, stream), daemon=True)
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
    ]
    sampler = Sampler(process.pid, interval, machine, origin)
    try:
        sampler.start()
        for reader in readers:
            reader.start()
        with process.stdin:
            process.stdin.write(payload)
        for reader in readers:
            reader.join()
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        wall = time.perf_counter() - origin
        samples = sampler.stop()
        footprint = peak_rss.peak_footprint_bytes(process.pid)
        _pid, status, usage = os.wait4(process.pid, 0)
    except BaseException:
        sampler.stop()
        process.kill()
        process.wait()
        raise
    process.returncode = os.waitstatus_to_exitcode(status)
    stderr = drained["stderr"].decode(errors="replace")
    lines = [line for line in drained["stdout"].decode(errors="replace").splitlines() if line.strip()]
    if process.returncode != 0 or not lines or not lines[-1].startswith("{"):
        raise SystemExit(
            f"the absorb child exited {process.returncode}: {stderr.strip() or 'it printed nothing'}"
        )
    child = ChildRun(
        process.returncode,
        drained["stdout"],
        stderr,
        wall,
        usage.ru_utime + usage.ru_stime,
        peak_rss.maxrss_to_bytes(usage.ru_maxrss),
        footprint,
        begin,
        samples,
        machine(),
    )
    return child, json.loads(lines[-1])


def measure_absorb_line(
    jobs: Mapping[str, bytes],
    names: Sequence[str],
    *,
    idle_s: float = IDLE_S,
    after_s: float = AFTER_S,
    interval_s: float = INTERVAL_S,
    env: Mapping[str, str] | None = None,
    meter: Callable[[], write_meter.WriteReading] = write_meter.read,
    machine: Callable[[], Machine] = read_machine,
    sleep: Callable[[float], None] = time.sleep,
    run: Callable[..., tuple[ChildRun, dict[str, Any]]] = run_absorb_child,
) -> dict[str, Any]:
    """Run one absorbs line between the write meter's readings, as `measure_step` runs a table step: the absorb's child and then the carry-and-append absorb's, each over its own copy of the line's segments (`jobs`), and return the line's measured fields (the module docstring's "The absorbs step"). Any segment in `names` a child left is unlinked. `meter`, `machine`, `sleep` and `run` are the write meter, the machine reading, the wait and the child, which a test replaces."""
    idle = None
    if idle_s > 0:
        idle = meter()
        sleep(idle_s)
    start = meter()
    ran: dict[str, tuple[ChildRun, dict[str, Any]]] = {}
    try:
        for writer in ("absorb", "carry"):
            ran[writer] = run(jobs[writer], env=env, interval=interval_s, machine=machine)
    finally:
        _unlink_segments(names)
    end = meter()
    rate = None if idle is None else idle_rate(idle, start)
    child, printed = ran["absorb"]
    carry, reference = ran["carry"]
    after: dict[str, object] | None = None
    if after_s > 0:
        sleep(after_s)
        later = meter()
        swapouts = _difference(child.begin.swapouts, machine().swapouts)
        after = write_fields(start, later, rate, swapouts)
    return {
        "absorb": printed["absorb"],
        "wall_s": round(child.wall, 3),
        "seconds": printed["seconds"],
        "cpu_s": round(child.cpu, 3),
        "peak_resident": child.peak_resident,
        "peak_footprint": child.peak_footprint,
        "samples": len(child.samples),
        "interval_s": interval_s,
        **moments(child),
        "file_rows": printed["rows"],
        "size": printed["size"],
        "sha256": printed["sha256"],
        "carry_and_append": {
            "wall_s": round(carry.wall, 3),
            "seconds": reference["seconds"],
            "cpu_s": round(carry.cpu, 3),
            "peak_resident": carry.peak_resident,
            "peak_footprint": carry.peak_footprint,
            "file_rows": reference["rows"],
            "size": reference["size"],
            "sha256": reference["sha256"],
        },
        "same": printed["sha256"] == reference["sha256"],
        "written": {
            "idle_s": idle_s,
            "idle_rate": None if rate is None else round(rate),
            "end": write_fields(start, end, rate, _difference(child.begin.swapouts, carry.end.swapouts)),
            "after_s": after_s,
            "after": after,
        },
    }


def absorbs_report(lines: Sequence[Mapping]) -> list[str]:
    """Return each path's fit against the standing file's rows, or the fresh rows for a path with no standing file (`scaling_sweep.fit`), of the absorb's peak footprint and resident, and of the carry-and-append absorb's peak footprint, with how many of the path's lines read the same bytes as the carry-and-append absorb."""
    report: list[str] = []
    for path in dict.fromkeys(line["path"] for line in lines):
        own = [line for line in lines if line["path"] == path]
        rows = [line["rows"] for line in own]
        if len(set(rows)) < 2:
            report.append(f"absorbs {path}: the fit needs two row counts; this run has {len(set(rows))}")
            continue
        figures = {
            "peak footprint": [line["peak_footprint"] for line in own],
            "peak resident": [line["peak_resident"] for line in own],
            "carry-and-append peak footprint": [line["carry_and_append"]["peak_footprint"] for line in own],
        }
        same = sum(1 for line in own if line["same"])
        report.append(
            f"absorbs {path} over {len(own)} lines (rows {min(rows)}..{max(rows)}), {same} the same as the carry-and-append absorb"
        )
        report.extend(
            f"  {label} ~ rows^{scaling_sweep.exponent(scaling_sweep.fit(rows, values))}"
            for label, values in figures.items()
        )
    return report


def run_absorbs(args: argparse.Namespace, tree: Path) -> int:
    """Run the absorbs holder's lines (the module docstring's "The absorbs step") under the held lock: clone the live `default` settle memo into the tree, make each distinct count's inputs once (`absorb_count`), a standing file only when a path of `--paths` reads one, then for each named count, in order, and each path of `--paths` measure one line (`measure_absorb_line`), recording each as it finishes, then the fit (`absorbs_report`). Every segment a line ships is unlinked as the line ends, however it ends (`_AbsorbSegments`), and the clone and every standing file the run wrote are removed as it ends, however it ends."""
    from rebuild.pipeline import oracle_cache, run_m1_gates
    from rebuild.review import build

    spec = spec_load.load_default_spec()
    live = conform.settle_memo_files(run_m1.OUT_DIR, spec, oracle_cache.settle_memo_inputs(run_m1.REPO_ROOT))[
        ABSORB_CONFIG
    ]
    if not conform.settle_memo_standing(live):
        raise SystemExit(f"{live.path} is not a settle memo under this tree's stamp; a cycle pass refills it")
    empty_tree(tree)
    directory = tree / ABSORBS
    directory.mkdir()
    clone = directory / "live.bin"
    store = conform._MemoStore()
    try:
        if not build._clone(live.path, clone):
            raise SystemExit(
                f"{live.path} could not be cloned without writing it; the absorbs holder writes no copy"
            )
        memo = replace(live, path=clone)
        store.load(memo, spec, None, lambda item: (item, "", ""))
        if store.retired or not len(store.reached):
            raise SystemExit(f"{clone} loaded no rows")
        source = _CountRows(store)
        units = sum(
            1
            for unit in run_m1_gates.sweep_units(conform.spec_alphabet(spec), conform.SWEEP_MAX_LENGTH)
            if unit.config == ABSORB_CONFIG
        )
        reads_standing = any(not path.startswith("fresh") for path in args.paths)
        counts: dict[int | str, AbsorbCount] = {}
        for name in dict.fromkeys(args.counts):
            _progress(f"{name}: making the inputs its lines read")
            counts[name] = absorb_count(name, source, live, clone, directory, units, reads_standing)
        absent = directory / "absent.bin"
        env = corpus_environment()
        head = _head()
        lines: list[dict[str, Any]] = []
        record = Record(args.out, ABSORBS)
        os.sync()
        time.sleep(SETTLE_S)
        token = os.urandom(4).hex()
        segments = _AbsorbSegments(directory / ABSORB_SEGMENTS)
        for number, name in enumerate(args.counts):
            count = counts[name]
            for path in args.paths:
                _progress(
                    f"{name} {path}: {args.idle_seconds:g} s idle, the two absorbs, then {args.after_seconds:g} s before the meter's last reading"
                )
                file, parts, sweep = absorb_job(path, count, live, absent)
                jobs: dict[str, bytes] = {}
                try:
                    for writer in ("absorb", "carry"):
                        prefix = f"s{token}-{number}-{ABSORB_PATHS.index(path)}{writer[0]}"
                        jobs[writer] = absorb_payload(writer, file, spec, parts, sweep, prefix, segments)
                    measured = measure_absorb_line(
                        jobs,
                        list(segments.names),
                        idle_s=args.idle_seconds,
                        after_s=args.after_seconds,
                        interval_s=args.interval,
                        env=env,
                    )
                finally:
                    segments.unlink()
                _warn_meter(f"{name} {path}", measured["written"])
                if not measured["same"]:
                    _warn(f"{name} {path}: the absorb's file differs from the carry-and-append absorb's")
                line: dict[str, Any] = {
                    "step": ABSORB_STEP,
                    "count": name,
                    "path": path,
                    "rows": count.rows,
                    "standing_rows": 0 if path.startswith("fresh") else count.rows,
                    "fresh_rows": sum(part.rows for part in parts),
                    "standing_bytes": (
                        None
                        if count.standing is None or path.startswith("fresh")
                        else count.standing.stat().st_size
                    ),
                    **measured,
                    "cpus": memory_budget.usable_cores(),
                    "ram": memory_budget.total_memory_bytes(),
                    "head": head,
                    "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                lines.append(line)
                text = json.dumps(line)
                print(text, flush=True)
                record.add(text)
        for text in ("", *absorbs_report(lines)):
            print(text, flush=True)
            record.add(text)
        return 0
    finally:
        store.close()
        shutil.rmtree(directory, ignore_errors=True)


def holder_sections(text: str) -> dict[str, list[str]]:
    """Split a record's lines by the holder each belongs to: a JSON line by its `step` (`HOLDER_OF_STEP`), and every other line, the fit, by the JSON line before it."""
    sections: dict[str, list[str]] = {holder: [] for holder in HOLDERS}
    holder = None
    for line in text.splitlines():
        if line.startswith("{"):
            holder = HOLDER_OF_STEP[json.loads(line)["step"]]
        if holder is not None:
            sections[holder].append(line)
    return sections


class Record:
    """The record at `path` as one holder's run rewrites it: every other holder's lines and fit as the file held them, and this run's lines and fit in place of the holder's earlier ones, holders in `HOLDERS` order. Making one drops the holder's earlier lines, and each `add` rewrites the whole file, so a run stopped partway keeps the lines it finished."""

    def __init__(self, path: Path, holder: str) -> None:
        self._path = path
        self._holder = holder
        self._sections = holder_sections(path.read_text(encoding="utf-8") if path.is_file() else "")
        self._sections[holder] = []
        self._write()

    def add(self, *lines: str) -> None:
        self._sections[self._holder].extend(lines)
        self._write()

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            "".join(f"{line}\n" for holder in HOLDERS for line in self._sections[holder]), encoding="utf-8"
        )


def _head() -> str | None:
    done = subprocess.run(
        ["git", "-C", str(ROOT), "describe", "--always", "--dirty", "--abbrev=8", "--exclude=*"],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() or None


def _warn(text: str) -> None:
    print(f"[warn] {text}", file=sys.stderr, flush=True)


def _progress(text: str) -> None:
    print(f"memory series: {text}", file=sys.stderr, flush=True)


def parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m rebuild.tools.memory_series",
        description="Measure what the M1 table build, the string replay, the review corpus build or a settle-memo absorb holds and writes at each of several alphabet cuts or row counts, one build at a time, writing nothing but its record.",
    )
    parser.add_argument(
        "--holder",
        choices=HOLDERS,
        default=TABLES,
        help="the build to measure: the table build (the default), the review corpus build, the string replay with its settle-memo absorbs, or the oracle's and the conformance sweep's settle-memo absorb",
    )
    parser.add_argument(
        "lines",
        nargs="*",
        metavar="k",
        help=f"the rune counts to cut alphabets at, for the corpus holder the load lines ({', '.join(f'{LOAD_PREFIX}{scale}' for scale in LOAD_SCALES)}) too, and for the absorbs holder the row counts, `{LIVE}` for the live default settle memo's own; by default {', '.join(map(str, SIZES))} below the whole alphabet and the whole alphabet for the table holder, {', '.join(map(str, REPLAY_SIZES))} below it and the whole alphabet for the replay holder, {', '.join(map(str, CORPUS_SIZES))} below it, the whole alphabet and both load lines for the corpus holder, and {' '.join(ABSORB_COUNTS)} for the absorbs holder",
    )
    parser.add_argument(
        "--paths",
        help=f"the absorbs to measure at each row count, comma-separated, of {', '.join(ABSORB_PATHS)}; every one by default (absorbs holder)",
    )
    parser.add_argument(
        "--configs",
        help="the settlement configurations to build or replay, comma-separated; every one by default (table and replay holders)",
    )
    parser.add_argument(
        "--replay-threads",
        type=int,
        help="how many walks run at once; by default the width run_m1 derives, `run_m1._replay_threads(None)` (replay holder)",
    )
    parser.add_argument(
        "--kernel-threads",
        type=int,
        help="the table build's width; by default the one a bare run_m1 derives (table holder)",
    )
    parser.add_argument(
        "--scratch-beside-default",
        type=int,
        help="at most how many deltas start from scratch beside default; by default as a bare run_m1 decides (table holder)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        help="the corpus build's units width; by default `corpus_job_budget(skip_gates=True)`, as a hand build takes it (corpus holder)",
    )
    parser.add_argument(
        "--signature-jobs",
        type=int,
        help="the corpus build's signature width; by default `signature_job_budget()` (corpus holder)",
    )
    parser.add_argument(
        "--write-jobs",
        type=int,
        help="the most workers the corpus build's write may start; by default `corpus_write_job_budget` of the units width (corpus holder)",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=IDLE_S,
        help=f"how long the device is read idle before each step, for device_net (default {IDLE_S:g}; 0 reads none)",
    )
    parser.add_argument(
        "--after-seconds",
        type=float,
        default=AFTER_S,
        help=f"how long after each step the meter is read again (default {AFTER_S:g}; 0 reads it only at the end)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=INTERVAL_S,
        help=f"seconds between the sampler's readings of the machine and, for the corpus holder, of the build's workers, and for the replay holder of each walk's crate and absorb worker, whose sums give the pair and combined peaks (default {INTERVAL_S:g})",
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / OUT_PATH, help=f"the record to write (default {OUT_PATH})"
    )
    args = parser.parse_args(argv)
    loads = {f"{LOAD_PREFIX}{scale}": scale for scale in LOAD_SCALES}
    named_loads = [text for text in args.lines if text in loads]
    if args.holder != CORPUS and named_loads:
        parser.error(f"{', '.join(named_loads)} name corpus lines; pass --holder {CORPUS}")
    args.counts = []
    args.paths = list(ABSORB_PATHS) if args.paths is None else args.paths.split(",")
    if args.holder == ABSORBS:
        if any(not text.isdecimal() and text != LIVE for text in args.lines):
            parser.error(f"an absorbs line is a row count or {LIVE}")
        args.counts = absorb_counts(args.lines)
        args.lines = []
        unknown = [path for path in args.paths if path not in ABSORB_PATHS]
        if unknown or not args.paths:
            parser.error(
                f"--paths names {', '.join(unknown) or 'nothing'}; the paths are {', '.join(ABSORB_PATHS)}"
            )
        if any(count != LIVE and int(count) < 1 for count in args.counts):
            parser.error("a row count is at least 1")
    elif args.paths != list(ABSORB_PATHS):
        parser.error(f"--paths applies to the {ABSORBS} holder")
    if any(not text.isdecimal() for text in args.lines if text not in loads):
        parser.error(f"a line is a rune count or one of {', '.join(loads)}")
    args.sizes = [int(text) for text in args.lines if text not in loads]
    args.loads = [loads[text] for text in named_loads]
    if any(size < 1 for size in args.sizes):
        parser.error("a size is a rune count of at least 1")
    other = {
        TABLES: (
            ("--jobs", args.jobs),
            ("--signature-jobs", args.signature_jobs),
            ("--write-jobs", args.write_jobs),
            ("--replay-threads", args.replay_threads),
        ),
        CORPUS: (
            ("--configs", args.configs),
            ("--kernel-threads", args.kernel_threads),
            ("--scratch-beside-default", args.scratch_beside_default),
            ("--replay-threads", args.replay_threads),
        ),
        REPLAY: (
            ("--kernel-threads", args.kernel_threads),
            ("--scratch-beside-default", args.scratch_beside_default),
            ("--jobs", args.jobs),
            ("--signature-jobs", args.signature_jobs),
            ("--write-jobs", args.write_jobs),
        ),
        ABSORBS: (
            ("--configs", args.configs),
            ("--kernel-threads", args.kernel_threads),
            ("--scratch-beside-default", args.scratch_beside_default),
            ("--replay-threads", args.replay_threads),
            ("--jobs", args.jobs),
            ("--signature-jobs", args.signature_jobs),
            ("--write-jobs", args.write_jobs),
        ),
    }[args.holder]
    stated = [flag for flag, value in other if value is not None]
    if stated:
        parser.error(f"{', '.join(stated)} do not apply to the {args.holder} holder")
    for flag, value in (
        ("--kernel-threads", args.kernel_threads),
        ("--replay-threads", args.replay_threads),
        ("--jobs", args.jobs),
        ("--signature-jobs", args.signature_jobs),
        ("--write-jobs", args.write_jobs),
    ):
        if value is not None and value < 1:
            parser.error(f"{flag} must be at least 1")
    if args.scratch_beside_default is not None and args.scratch_beside_default < 0:
        parser.error("--scratch-beside-default must be zero or more")
    if args.idle_seconds < 0 or args.after_seconds < 0:
        parser.error("--idle-seconds and --after-seconds must be zero or more")
    if args.interval <= 0:
        parser.error("--interval must be more than zero")
    return args


def _warn_meter(name: str, written: Mapping[str, Any]) -> None:
    for label in ("end", "after"):
        block = written[label]
        if block is None:
            continue
        if block["basis"] != "own":
            _warn(f"{name} {label}: the write meter read the {block['basis']} basis, not own")
        if "warning" in block:
            _warn(f"{name} {label}: {block['warning']}")


def run_corpus(args: argparse.Namespace, tree: Path) -> int:
    """Run the corpus holder's lines (the module docstring's "The corpus step") under the held lock: clone and check the inputs, then measure each cut line and each load line in turn, recording each as it finishes, then the corpus fit (`corpus_report`). A line that leaves a stray file stops the run once its line is recorded. Each line reads the checkout's code digests just before its build's spawn and once the child is reaped (`measure_corpus_line`), and a reading that moved during the build, or since the first line's, prints a `[warn]` line naming the line. The corpus step's part of the tree, the clones among it, is removed as the run ends, however it ends."""
    from rebuild.review import audit, build

    kernel_exec.cargo_build()
    empty_tree(tree)
    try:
        inputs = clone_inputs(ROOT, tree / CORPUS / "inputs")
        identity = check_inputs(inputs)
        spec = spec_load.load_spec(*(inputs.spec_root / relative for relative in SPEC_PATHS[:3]))
        if args.lines:
            sizes, scales = args.sizes, args.loads
        else:
            sizes, scales = default_sizes(len(scaling_series.series_order(spec)), CORPUS_SIZES), list(
                LOAD_SCALES
            )
        cuts = corpus_cuts(spec, sizes, scales)
        widths = corpus_widths(args.jobs, args.signature_jobs, args.write_jobs)
        live = json.loads(inputs.manifest.read_text(encoding="utf-8"))
        routes = null_routes(
            live, [*(entry.id for entry in audit.load_ledger(inputs.ledger)), *audit.RESERVED_CLASS_IDS]
        )
        env = corpus_environment()
        head = _head()
        first_code: dict[str, str] | None = None
        lines: list[dict[str, Any]] = []
        record = Record(args.out, CORPUS)
        os.sync()
        time.sleep(SETTLE_S)
        for cut in cuts:
            _progress(
                f"{cut.name}: {args.idle_seconds:g} s idle, the build, then {args.after_seconds:g} s before the meter's last reading"
            )
            measured = measure_corpus_line(
                inputs,
                tree / CORPUS / cut.name,
                cut,
                widths,
                routes,
                env=env,
                cwd=build.REPO_ROOT,
                idle_s=args.idle_seconds,
                after_s=args.after_seconds,
                interval_s=args.interval,
            )
            if first_code is None:
                first_code = measured["code"]
            elif measured["code"] != first_code:
                _warn(f"{cut.name}: the checkout's corpus code or crate binary moved since the first line")
            if "code_after" in measured:
                _warn(f"{cut.name}: the checkout's corpus code or crate binary moved during the build")
            _warn_meter(cut.name, measured["written"])
            if not cut.load and measured["signatures_shaped"]:
                _warn(
                    f"{cut.name}: the load shaped {measured['signatures_shaped']:,} signatures, so the live store did not answer every window"
                )
            line: dict[str, Any] = {
                "step": LOAD_STEP if cut.load else CORPUS_STEP,
                "runes": len(cut.runes),
                "letters": cut.letters,
                "ligs": len(cut.runes) - cut.letters,
                **({"scale": cut.scale} if cut.load else {}),
                **widths,
                **measured,
                "alphabet": list(cut.runes),
                "inputs": identity,
                "cpus": memory_budget.usable_cores(),
                "ram": memory_budget.total_memory_bytes(),
                "head": head,
                "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            lines.append(line)
            text = json.dumps(line)
            print(text, flush=True)
            record.add(text)
            stray = measured["files"]["stray"]
            if stray:
                raise SystemExit(
                    f"{cut.name} left files the corpus step does not route or expect, so the line wrote more than it should: "
                    f"{', '.join(f'{name} ({size:,} B)' for name, size in stray.items())}; the line is in {args.out}"
                )
        for text in ("", *corpus_report(lines)):
            print(text, flush=True)
            record.add(text)
        return 0
    finally:
        shutil.rmtree(tree / CORPUS, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(argv)
    if spec_load.M1_RUNES_ENV in os.environ:
        raise SystemExit(
            f"the memory series refuses: {spec_load.M1_RUNES_ENV} is set ({os.environ[spec_load.M1_RUNES_ENV]!r}), "
            f"and the series cuts its own alphabets from the whole store; unset it"
        )
    if args.holder == CORPUS and memory_tally.TALLY_ENV in os.environ:
        raise SystemExit(
            f"the memory series refuses: {memory_tally.TALLY_ENV} is set, and the debug tally keeps alive the "
            "collections it reads, which would move every corpus reading; unset it"
        )
    with artifact_cycle.stop_signals():
        try:
            return run_series(args)
        except artifact_cycle.CycleStopped as stopped:
            return 128 + stopped.signum


def run_series(args: argparse.Namespace) -> int:
    """Hold the series' lock (`store_lock.hold_flock` on `<tree>.lock`), refusing when another run holds it, and run the holder `args` names: the corpus holder's lines (`run_corpus`), the replay holder's (`run_replay`), the absorbs holder's (`run_absorbs`), or the table holder's steps and then the whole-series fit (`report`)."""
    configs = configs_from(args.configs) if args.configs else list(conform.SETTLEMENT_CONFIGS)
    tree = ROOT / TREE_PATH
    lock = store_lock.lock_path_for(tree)
    with ExitStack() as held:
        try:
            held.enter_context(store_lock.hold_flock(lock, blocking=False))
        except store_lock.LockBusy:
            raise SystemExit(f"another memory series holds {lock} and is using {tree}") from None
        if args.holder == CORPUS:
            return run_corpus(args, tree)
        if args.holder == REPLAY:
            return run_replay(args, tree)
        if args.holder == ABSORBS:
            return run_absorbs(args, tree)
        spec = spec_load.load_default_spec()
        order = scaling_series.series_order(spec)
        sizes = args.sizes or default_sizes(len(order))
        kernel_exec.cargo_build()
        binary = prepare_tree(tree, kernel_exec.BINARY)
        cuts = []
        for size in sizes:
            cut = scaling_series.sub_spec(spec, order, size)
            spec_path = tree / f"spec-r{len(cut.runes)}.json"
            kernel_io.write_spec(cut, spec_path)
            cuts.append((cut, spec_path))
        flags = bare_flags(
            configs,
            threads=args.kernel_threads,
            scratch=args.scratch_beside_default,
        )
        env = {**os.environ, **kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT} if sys.platform == "darwin" else None
        head = _head()
        lines: list[dict[str, object]] = []
        record = Record(args.out, TABLES)
        os.sync()
        time.sleep(SETTLE_S)
        for cut, spec_path in cuts:
            runes = len(cut.runes)
            letters = sum(1 for rune in cut.runes.values() if not rune.sequence)
            _progress(
                f"r{runes}: {args.idle_seconds:g} s idle, the build, then {args.after_seconds:g} s before the meter's last reading"
            )
            measured = measure_step(
                binary,
                spec_path,
                tree / f"r{runes}",
                configs,
                flags,
                f"{STAMP_PREFIX}{runes}",
                idle_s=args.idle_seconds,
                after_s=args.after_seconds,
                interval_s=args.interval,
                env=env,
            )
            _warn_meter(f"r{runes}", measured["written"])
            line: dict[str, object] = {
                "step": TABLES,
                "runes": runes,
                "letters": letters,
                "ligs": runes - letters,
                "configs": list(configs),
                **asdict(flags),
                "modes": " ".join(kernel_exec.mode_flags()) or "default modes",
                **measured,
                "alphabet": sorted(cut.runes),
                "cpus": memory_budget.usable_cores(),
                "ram": memory_budget.total_memory_bytes(),
                "head": head,
                "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            lines.append(line)
            text = json.dumps(line)
            print(text, flush=True)
            record.add(text)
        for text in ("", *report(lines)):
            print(text, flush=True)
            record.add(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
