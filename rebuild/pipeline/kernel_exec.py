"""The kernel interface: the Python side of the Rust kernel. It builds the binary, runs the table build and the stream fan-out, reads the section 5.7 guard verdicts, settles batched windows for explain, review, conform and the tests, runs the string and shipped-order replays, and returns single-configuration products and tables to callers other than `run_m1`. It lives in the pipeline because the pipeline calls it. The `rebuild/tools/kernel_*.py` scripts are separate measurement harnesses.

The semantics defaults live here beside the flags that pass them to the kernel. `SIMULATED_PROSPECT_DEFAULT` and `FOLLOWER_PREFER_SLOTS_DEFAULT` control settlement, and `DEEP_CLASSES_DEFAULT` controls enumeration. Each is a module attribute read at call time, so a process sets them through the environment and a test can monkeypatch them. A caller that wants a different mode set builds a `SettlementModes` and passes it to `settle_cases`, `settle_windows` or `settle_sequences`.

The build is `cargo build --release` against the crate's manifest. Release is the only profile anything in the repository runs: the pipeline and the spec-echo test in `rebuild/test_kernel_io.py` both run `target/release/ams-m1-kernel`, and a debug binary is too slow to substitute for it. A machine without `cargo` gets a `KernelBuildError` that says how to install it. `ensure_built` builds once per process, and every caller in the process shares that build.

`build_table_files` is what `run_m1` calls. One `build-tables` process enumerates every settlement configuration and folds each in place. It writes each configuration's settlement TSV, join TSV and plain window enumeration, and returns the table digest of each configuration's pair of tables with the crate's `[t]` lines and the process's own resident peak and peak footprint (`TableBuild`). No transition stream is written, because the fold reads the product the worklist still holds; that saves writing, reading and parsing several hundred megabytes per configuration. The configurations after `default` are deltas over it. `default` enumerates first and keeps its trace memo, and each other configuration reads that memo for every window whose key names none of its own unlocking runes and whose settlement read none of them, and traces only the rest. Where memory has room, the heaviest of them enumerate from scratch beside `default` instead, from the start (`deltas_from_scratch`). The memo is also carried across builds in the `memo-<config>.tsv.gz` files packed beside the tables. The next build reuses a memoized window only if its settlement read no rune whose content has changed and no predicate class whose membership has changed. `rebuild/kernel-rs/src/memo.rs` gives the argument, and `run_m1.previous_memos` decides which files may be read. Before any table is written, the configurations exchange windows (`rebuild/kernel-rs/src/crossconfig.rs`): each configuration's table takes in the windows another configuration keeps live where its own rules would answer them wrongly ahead of every right answer in the order the font ships, so a configuration's tables depend on the whole set it was built with. The windows head lists what each table took in (`table.DecisionTable.imports`), and a guard rule that only such a window reaches carries a certificate the witness stage settles under the configuration the window is live in (`witness.GUARD_MARKER`).

`enumerate_configs` is the stream fan-out. One process enumerates every named configuration and writes each one's transition stream to its own file. Nothing on the build's path calls it; `enumerate_transitions` and the tests do.

The table build's width is limited by memory, because a live configuration holds its whole working set until it has emitted. Each configuration runs on its own thread from its enumeration to its files, and its prepared product stays parked from its preparation until the cross-configuration exchange ends, so at the end of the delta wave every configuration's parked product is resident beside the last delta's enumeration (`doc/parallelism.md`). `table_build_booking_bytes` is the memory a build at a given width is booked at: `DEFAULT_MEMO_BYTES` for the `default` memo snapshots kept alive for the wave, `PARKED_FOLD_BYTES` for each configuration's parked product, `DELTA_SLOT_BYTES` for each delta in flight beyond its parked product, and `FOLD_PREPARATION_BYTES` for a slot that runs only `default`'s fold preparation, which a width above the delta count has. A build in which every configuration enumerates from scratch books each slot at `SCRATCH_PEAK_BYTES` less its parked product instead, and so does each delta a build starts from scratch beside `default`, in place of a delta's slot; such a build is booked at no less than its start, one `SCRATCH_PEAK_BYTES` for `default` and one for each of them. `kernel_threads_default` is the widest width, up to the configuration count, whose booking fits the machine's memory less the reserve that policy states, floored at one; `memo_writes_overlap` and then `deltas_from_scratch` decide the memo-write order and how many deltas start from scratch beside `default` at that width, and neither narrows it. A larger machine, or a container limited by its cgroup, gets its own width without editing a constant. `AMS_KERNEL_THREADS` overrides the arithmetic in either direction. The artifacts are byte-identical at any width, so the override changes only memory use. Callers cap the width, a stated one included, at the number of settlement configurations (`conform.SETTLEMENT_CONFIGS`; the overlay configuration is never enumerated) and at the usable cores. A build that shares `default`'s memo writes its memo files on threads of their own beside the delta wave and the folds (`build_table_files`' `overlap_memo_writes`) when `memo_writes_overlap` finds that its booking at its width, with `MEMO_WRITE_OVERLAP_BYTES` added for what those writers hold, still fits; otherwise it writes each file ahead of the work that follows it, which costs time and no memory. The files are the same bytes either way. The string replay after a build has its own divisor, `REPLAY_PEAK_BYTES`. `replay_threads_default` divides by it with nothing subtracted first, because the replay's engines are built after the build process has exited and no memo outlives it. `AMS_REPLAY_THREADS` overrides it.

`build_tables` and `enumerate_transitions` are the single-configuration forms, and neither writes anything that outlives the call. A single configuration has no other configuration to take windows from, so `build_tables` gives the same windows and joins as a build of the whole set but can give a different settlement table. Each dumps one spec to a scratch directory. `build_tables` runs `build-tables` into that directory and reads the two tables back through `table.read_windows` and `table.read_join_tsv`. `enumerate_transitions` enumerates the raw product as a stream and parses it into a `table.FixpointProduct`. Only tests call either.

`guard_sweep` runs `guard-sweep` once and returns the complete mapping from `(ligature, first raw slot, second raw slot)` to the configuration-independent formation verdict. It is memoized per spec identity, so a process sweeps a spec once however many callers ask. `guard_sweep_under` returns the same mapping for one named configuration instead of quantifying over the powerset. It is not memoized, because only tests read it; they compare each configuration's verdicts with the quantified ones.

The settlement and replay functions share the memoized spec dump. `replay_strings` is the enumeration-completeness check `run_m1.run_replay_strings` runs after every table build. One `replay-strings` process reads the settlement TSVs the build wrote and walks every text up to the maximum length, or only the texts naming the families the caller says changed, checking each window against the crate's own settlement. A disagreement raises `ReplayDisagreement` with the crate's message, which names the configuration, the window and the text. `settle_cases` is the raw form. It writes one tab-separated case line per independent window (`case_line` writes one) and returns the full Rust trace for each. It checks the case result count and that each output line starts with its case line verbatim, and decodes each distinct result once however many windows returned it. `settle_windows` asks the crate for the settled record alone (seven tab-separated fields under `--settled-only`) and decodes each straight to a `Settled`. The conform walker and the ligature outgoing check use it because they need only outcomes, not traces. Like `settle_sequences`, it takes an `on_error` argument, so a caller prefilling windows it may never read can get `None` for a refused window and keep the rest of the batch, and its `tolerate` argument gives `None` for only the refusal buckets it names. `settle_sequences` is what explain, the probe and the review corpus call. The subcommand takes independent windows, but a sequence's next left context is the previous window's result, so a batch of sequences advances in waves: every sequence's first position, then every second position using the first wave's results. Boundary positions are settled locally because their results are model constants. `settle_codepoints` settles one text. The CLI writes boundary tokens as `edge`, `space`, `zwnj`, `namer-dot` and `unknown`. The guard parser converts them to the `RightToken` constants in `settle`, so consumers never confuse these model tokens with glyph names such as `uni200C` or `periodcentered`.

The codecs between the transport lines and the pipeline's model types live here too, because every settlement caller needs them: `case_line` for case lines, and `trace_of` and `_settled_of_fields` for case results. A window the crate refuses returns `{raise, message}`. That becomes a `settle.SettleError` carrying the crate's error code as its bucket and its message verbatim, so a caller can sort refusals without parsing text. Any other malformed case result means the kernel interface itself is wrong and raises `KernelRunError`.

Every invocation is waited for up to a limit, and one that has not answered by then is killed and raises `KernelRunError` (`_run_kernel`'s `timeout`). The verbs that answer in seconds or minutes at any alphabet size wait `TIMEOUT`. `build-tables` and `replay-strings` grow with the alphabet, so each derives its limit at the call from the size of the spec's alphabet (`build_tables_timeout`, `replay_timeout`), and neither waits less than `TIMEOUT`.

Every invocation's result is checked strictly against the CLI contract. Exit 2 is a usage error, which for a well-formed invocation means the subcommand is missing or the flag sets on the two sides have diverged. Any other nonzero exit is the kernel rejecting its inputs. Output on stderr after a clean exit is a failure unless timings were requested. In that case every `[t]` line is copied to this process's stderr verbatim, so the cycle journal records the kernel's per-configuration wall-clock times the same way it records Python's, and any other stderr line is still a failure. Enumeration writes its results to files, so any stdout output there is a failure. `build-tables` also writes files but reports its digests on stdout, one JSON object per line, and the configurations they name are checked against the ones requested. `guard-sweep` writes every verdict to stdout as TSV, which is parsed strictly.
"""

from __future__ import annotations

import fcntl
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from rebuild.pipeline import kernel_io, labels, settle, table
from rebuild.pipeline.model import CellId, Provenance, ResolvedSpec, Settled, feature_config_token
from rebuild.pipeline.table import DecisionTable, FixpointProduct, JoinTable
from rebuild.tools import memory_budget
from rebuild.tools.peak_rss import ReapedPeaks, format_gb, reap_peaks_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
BINARY = REPO_ROOT / "rebuild" / "kernel-rs" / "target" / "release" / "ams-m1-kernel"
MANIFEST = REPO_ROOT / "rebuild" / "kernel-rs" / "Cargo.toml"
# What one delta in flight adds to the table build's peak beyond its own parked product (PARKED_FOLD_BYTES), when it runs over default's memo: the per-slot term of `table_build_booking_bytes`, and the divisor of `kernel_threads_default` below the configuration count. A delta traces only the windows default's memo cannot answer, so it costs less than a configuration enumerated from scratch (SCRATCH_PEAK_BYTES). Its `--cache-stats` lines show how much: `shared_memo_hits` counts the windows it takes from that memo, and its trace_cache length counts what it traces beyond it. With ·Way in the alphabet, default traces 28.58M windows itself, and beyond its memo `ss03` traces 21.39M, `ss03+ss05` 20.67M, `ss05` 4.82M and `ss04` 4.02M. What a slot costs depends on what runs beside it, so the term is measured on the whole wave, not on one configuration built alone. The deltas run heaviest first (`fanout::delta_worklist`): two and three wide, `ss03` and `ss03+ss05` reach their peaks together while few configurations have parked, and four and five wide, `ss05` and `ss04` finish and park before the heavy two peak. The reading is one `build-tables` over every settlement configuration with memo output, `--timings` and `--cache-stats`, from scratch, under `/usr/bin/time -l` on an idle 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`). With ·Way in the alphabet and the snapshot's tagged blocks (`memo::SnapshotEntries`) it peaks at 11.80 GB one wide, 16.66 and 16.41 GB two wide, 18.68 GB three wide, 23.88 GB four wide, and 20.67 and 20.78 GB five wide, and at 21.53 GB five wide reading the previous build's memo files with ·Way edited. Each has a `peak memory footprint` line within two gigabytes of its resident figure (11.00, 16.01, 15.81, 18.12, 23.37, 20.16, 20.26 and 21.01 GB). That two-gigabyte window is the condition for using a resident figure at all, because the resident line under-reads a wave by whatever the memory compressor has taken from the process; a reading whose footprint is gigabytes above its resident line measures the compressor, not the build. Net of DEFAULT_MEMO_BYTES, a PARKED_FOLD_BYTES per configuration and, five wide, FOLD_PREPARATION_BYTES, those peaks are 1.50, 3.18, 3.06, 2.79, 3.40, 2.07, 2.10 and 2.28 GB per delta slot. The readings come from a crate that published no liveness verdicts, while DEFAULT_MEMO_BYTES books 0.10 GB of them, so each of those figures understates its slot by 0.10 GB divided by the width. The peak moves between otherwise identical runs with how much of the enumerations' freed memory the allocator still holds: the two five-wide readings differ by 0.10 GB and the two two-wide readings by 0.25 GB. The run_m1 step peaks in `rebuild/out/cycle-timings.ndjson` do not measure this build, because on both fleet machines every run_m1 writes its memo files beside the wave, which MEMO_WRITE_OVERLAP_BYTES books, and one that is bare or skips the gate also starts the heaviest deltas from scratch beside default, which SCRATCH_PEAK_BYTES books. Booking every reading at least the wider of those spreads above its peak takes 3.46 GB a slot, which the four-wide reading needs, or 3.5 GB rounded up to the tenth. The constant stays at the 4.7 GB that rule gave on the crate before the snapshot's tagged blocks, whose two five-wide readings differed by 1.54 GB, because two runs at a width do not bound the spread; at 4.7 GB every width is booked 3.04 GB or more above what it measured: 15.0 GB one wide, 19.7 GB two wide, 24.4 GB three wide, 29.1 GB four wide and 31.3 GB five wide. It errs high on purpose: a per-unit estimate that is too low puts the machine into swap, while one that is too high only narrows the wave. With the other terms as they stand, a value of 7.09 GB or more costs the 18-core machine its whole wave under a gated cycle, with gate:make-test's pytest pool running, 7.31 GB or more costs the 12-core machine its whole wave under a gated cycle, and 7.76 GB or more costs both machines their whole wave alone; `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` check those widths. The same build takes 209.8 and 215.8 s five wide, 220.6 s four wide, 243.2 s three wide, 290.3 and 293.4 s two wide and 432.2 s one wide. Re-measure this constant, FOLD_PREPARATION_BYTES, SCRATCH_PEAK_BYTES, DEFAULT_MEMO_BYTES, PARKED_FOLD_BYTES and MEMO_WRITE_OVERLAP_BYTES together, and TABLE_BUILD_PEAK_BYTES from them, whenever the alphabet grows or a per-configuration working set changes, on an idle 48 GiB machine with nothing else running and one build at a time: the whole-wave build above, writing its memo files in line, at one through five wide, two and five wide a second time, and five wide once more reading the five-wide build's memo files with the newest letter edited (what an artifact-cycle pass after a rune edit runs); then each settlement configuration alone, the two heaviest, `ss03` and `ss03+ss05`, twice (SCRATCH_PEAK_BYTES), with the resident size of the build of `default` alone sampled through its fold (FOLD_PREPARATION_BYTES). Then MEMO_WRITE_OVERLAP_BYTES by the paired runs its comment describes, and the deltas started from scratch beside default by the runs SCRATCH_PEAK_BYTES's comment describes.
DELTA_SLOT_BYTES = 4_700_000_000
# What the slot that runs only default's fold preparation holds beyond default's parked product: the last term of `table_build_booking_bytes` at a width above the delta count. Default's preparation (its expansion, row chains and first rules) takes a worker slot when the delta wave starts (`fanout::run_configs_tables`). Above the delta count that slot runs nothing else; at or below it, a delta takes the slot when the preparation ends, so there the slot is booked as a delta's. The preparation consumes default's enumeration product and leaves the parked product PARKED_FOLD_BYTES counts, so beyond that product it holds at most the product it starts from and whatever freed memory the allocator still holds beside it. With ·He in the alphabet, one `build-tables --configs=default --threads=1` with its memo output and `--cache-stats` on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`) holds 2.67 to 2.74 GB once its memo file is written, in ten runs with the trace memo's key hashed as packed words or by its derived `Hash`, and its malloc zones hold 2.77 to 2.79 GB allocated there in the nine of them probed with `/usr/bin/heap` (`var/keep/issue-519/`). Its sort raises that to 3.31 to 3.37 GB with 2.03 to 2.05 GB allocated, because the allocator keeps the drained transition map's 1.38 GB table resident (vmmap's `Malloc Large (empty)`). Sampled every 0.2 s in five of the runs, the preparation starts beside that table and reaches 3.75 to 3.79 GB, the highest sample after the memo write, until the allocator lets the table go about a second later (2.36 to 2.42 GB); it then rises to its `resident_parked` 2.82 to 2.88 GB, 1.49 to 1.51 GB allocated. The constant is the highest sample less PARKED_FOLD_BYTES, rounded up to the tenth, so the two terms together cover what default holds from its sort to its rendezvous. With the other terms as they stand, a value of 2.64 GB or more costs a bare run_m1 on either fleet machine its deltas from scratch beside default (`deltas_from_scratch` returns one, which the crate, starting only whole tiers, rounds down to none); `rebuild/test_memory_budget.py` checks that count. It is at most DELTA_SLOT_BYTES, which `rebuild/test_memory_budget.py` checks, so a width at or below the delta count, where the preparation and the deltas share the slots, is booked at its deltas alone. In a wave the preparation ends seconds after the deltas start, long before any of them peaks, so this term errs high, and it does not book the more that the allocator can hold there: one `ss04` build alone, under the probe that lists the process's memory regions, held 4.69 GB resident after its memo write with 2.66 GB allocated.
FOLD_PREPARATION_BYTES = 2_200_000_000
# The peak of one configuration enumerated from scratch and alone, through its memo write and its fold: what a slot holds, with its parked product, in a build where every configuration enumerates from scratch (`table_build_booking_bytes` with `from_scratch`): a narrowed `rebuild/tools/scratch_build.py` set without `default`, or `default_memo_sharing=False`. A build that includes `default` and at least one delta enumerates `default` from scratch before the wave, under this bound, alone or beside the deltas `deltas_from_scratch` starts from scratch with it, and `table_build_booking_bytes` books that start at one such bound for `default` and one for each of them, and each of them in the wave at this bound less its parked product. Measured with ·He in the alphabet, the trace, prospect and candidates keys packed onto ordinals and hashed as packed words, the trace entry at sixteen bytes (`rebuild/kernel-rs/src/engine.rs` asserts each size) and the snapshot's tagged blocks (`memo::SnapshotEntries`): each settlement configuration alone at `--threads=1`, from scratch, with its memo write and its fold, under `/usr/bin/time -l` on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), peaks at 9.69 to 9.70 GB for `ss04` in five runs, 9.13 GB for `ss03+ss05` twice, 8.98 to 9.27 GB for `default` in six runs, 9.01 to 9.08 GB for `ss03` in eight runs and 9.04 GB for `ss05`, each run's `peak memory footprint` line within 0.01 GB of its resident figure (`var/keep/issue-519/`). With the keys' derived `Hash`, `ss03` peaks at 8.81 to 9.08 GB in eight runs, five of them at 8.81 to 8.91 GB, below every run with the packed hashes, and `default` at 9.00 to 9.15 GB in four, so the hash does not set the highest reading. The whole-process reading is one slot's cost, with no separate allowance for the process: what the slots share is the parsed spec and its index, which the process holds in about 0.1 GB before a configuration's engine is built. Every configuration's trace memo outgrows a table of 2^25 buckets, which holds 29.36M windows (`ss04` traces 30.55M and `ss03` 32.73M), so each ends its enumeration in a table of 2^26 buckets, 2.48 GB. The peak is at the release point, with every memo table built: the `resident_before_release` cache-stats line reads at most 0.22 GB below the peak of each run with the cache. There the process's malloc zones hold 6.98 GB allocated for `ss04`, 7.10 to 7.12 GB for `default` and 7.13 to 7.15 GB for `ss03` (`/usr/bin/heap`), within 0.03 GB from run to run and 1.44 to 2.65 GB below the resident size. Beside them, vmmap's full listing, taken in one run each of `ss03`, `default` and `ss04`, counts 1.70 to 2.94 GB resident in freed large allocations that the allocator keeps until the kernel reclaims them at a time of its own choosing (`Malloc Large (empty)`), chiefly blocks whose sizes match earlier tables of the trace memo (1.24 GB, or the 0.62 GB one before it), the transition map (0.69 GB) and the prospect memo (0.55 GB); that is 0.25 to 0.29 GB more than the same run's gap, because 0.26 to 0.30 GB of the allocated bytes is not resident. How much of the freed memory is still held is what moves the peak between otherwise identical runs. It is resident and in the footprint while it is held, so the constant books it, rounding the highest reading up to the tenth. One `ss03` run under a probe that lists the process's memory regions read 9.75 GB: its resident size rose 1.18 GB above its release-point reading while the process was stopped in the probe and ran none of its own code, so the constant leaves that reading out. With `MallocLargeCache=0` in the environment, where the allocator returns each freed large allocation to the system at once, the same builds peak at 7.25 GB for `ss04`, 7.27 GB for `default` and 7.43 GB for `ss03` twice, each within 4 s of the same build's time with the cache. `resident_after_sort` reads 3.17 to 3.42 GB in every run with the cache but one `ss04` run whose allocator still held 2.4 GB there, so the drain of the transitions, their sort and the fold stay below the peak, and a narrower memo key or entry lowers this bound by as much as it shrinks the tables. The deltas started from scratch beside default peak together with default near the end of their enumerations, which is why the start is booked at one bound for each. The reading is one five-wide `build-tables` over every settlement configuration with memo output, `--timings`, `--cache-stats` and `--overlap-memo-writes`, from scratch, under `/usr/bin/time -l`, with none, two and all four of the deltas beside default (the crate starts whole tiers of equal unlocking-rune count, `fanout::scratch_tiers`, so never one or three), and the same build with all four without `--cache-stats`, the argument list of a run_m1 that starts all four, in alternating rounds, eight or more of each, then at none, two and four reading a round's memo files at the same count with the newest letter edited, twice, on the 18-core M5 Pro 48 GiB MacBook Pro with its desktop open as usual and no other build running (`var/keep/issue-516/`, whose `report.py` reads the logs). With ·Way in the alphabet, the memo keys hashed as packed words and default publishing its liveness verdicts to the deltas that read its memo (none, with all four from scratch), the higher of each run's resident and footprint lines reads 27.87 to 30.18 GB with all four in twenty runs, 16.50 to 19.64 GB with two in twelve, 19.51 to 20.19 GB with none in eight, and 26.09 and 26.31, 19.28 and 19.35, and 17.54 GB twice reading previous memos with four, two and none. No run swapped, but every build with all four ran the machine's free pages down to their floor (0.01 to 0.05 GB) and the memory compressor took part of it: its footprint line reads from 0.08 GB below to 5.48 GB above its resident line, so the footprint is the reading, because the resident line leaves out what the compressor holds. In the seven runs whose footprint line is within 0.2 GB of their resident line, the readings are 28.03 to 28.18 GB. The four readings above 28.29 GB, 29.50 to 30.18 GB, are the four fresh runs in which the compressor's occupancy passed 14 GB (14.08 to 16.09 GB, against 13.14 GB or less in the other sixteen), and in the samples they reach their footprint peaks earlier, 75 to 76 s into the build against 79 s or later in the other sixteen. At those peaks the compressor holds 5.10 to 11.04 GB of the build's own pages, its sampled footprint less its resident size, so its occupancy counts the build beside the rest of the machine, and these runs do not separate the two. On the crate with the snapshot's tagged blocks and the derived key hashes, one fresh run with all four read 36.97 GB, its footprint line 8.93 GB above its resident line (`var/keep/issue-511/shipped-final/`). With ·He in the alphabet, the same fresh build with all four and `--cache-stats` reads 38.31 and 39.36 GB, its footprint lines 12.77 and 9.68 GB above its resident lines (`var/keep/issue-519/`), and the first cycle pass with the footprint line read 38.35 GB (`var/keep/issue-520/`). One SCRATCH_PEAK_BYTES per configuration is enough for the start on these readings: the highest at 45 runes, 39.36 GB, is 7.87 GB a configuration, and the 36.97 GB one is 7.39 GB, both under the 9.7 GB booked for each; `var/keep/issue-516/report.py` prints each count's start, booking and least margin against the constants as they stand. With the other terms as they stand, a value of 7.44 GB or more leaves the 18-core machine under a gated cycle, with gate:make-test's pytest pool running, only the heaviest tier from scratch (`ss03` and `ss03+ss05`), and 8.57 GB or more none; on the 12-core machine those are 7.66 and 9.02 GB, and alone, on either, 8.11 and 9.92 GB. So at this value both machines start the heaviest tier from scratch beside default under a bare run_m1 and no delta from scratch under a gated cycle, at width five with the memo files written beside the wave; `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` check those counts. Re-measure the runs above with DELTA_SLOT_BYTES.
SCRATCH_PEAK_BYTES = 9_700_000_000
# The memory of the default memo snapshots shared by the delta wave, the first term of `table_build_booking_bytes`. `fanout::run_configs_tables` keeps the fresh default snapshot and, when present, the previous build's default memo alive together. Loading a previous memo drops keys naming edited runes, and lookup and memo writing apply the full read-journal exclusion, so a loaded previous memo keeps windows that default traces again, and together the two can hold up to two default-sized snapshots. `memo::SnapshotEntries` holds thirty-six-byte key/entry records and a thirty-two-byte block of hash tags per sixteen records, two bytes a record, with the pools beside them and no hash-table slack. With ·Way in the alphabet, default's `--cache-stats` trace_cache length (28.58M windows in every fresh whole-wave reading DELTA_SLOT_BYTES cites) puts one snapshot's records and blocks at 1.09 GB, and the memos hold no elimination text. Where some delta reads the fresh snapshot, it also carries the liveness verdicts default published for those deltas (`fanout::run_configs_tables`), whose record table, slots and fired deltas `--cache-stats` reports as `published_verdicts`: 0.10 GB one wide over every settlement configuration, which publishes them for all three of the deltas' unlocking-rune sets (`var/keep/issue-514/`). A memo read from a file carries none. The constant is two such snapshots and those verdicts, rounded up to the tenth. A fresh build holds one; the five-wide build reading previous memos with ·Way edited holds the fresh snapshot of the 1.72M windows default traced again beside the filtered previous memo. Writing the memo files beside the wave adds no third: default's writer reads the snapshot the wave reads, through the same `Arc`, and in every overlapped reading MEMO_WRITE_OVERLAP_BYTES cites default's trace_cache length is the in-line twin's, 28.58M fresh. What that writer holds beyond the snapshot, its rows and interned tables, MEMO_WRITE_OVERLAP_BYTES books.
DEFAULT_MEMO_BYTES = 2_300_000_000
# What one configuration's prepared fold product holds while it waits for the cross-configuration exchange (`fanout::run_configs_tables`, `rebuild/kernel-rs/src/crossconfig.rs`): the class rows, their label-grain expansion, the row chains, the window options, its first rules and its source-row index, from its fold's preparation until the exchange ends. Every configuration's parked product is resident at once at the exchange, so a wave that reuses a worker slot holds the finished configurations' products beside the deltas still enumerating; `table_build_booking_bytes` books one per configuration, and DELTA_SLOT_BYTES and FOLD_PREPARATION_BYTES each count a slot beyond its own configuration's product. Most of it is the class rows and their expansion, whose row holds pool ids rather than strings (`fold::FoldRow`, sixteen bytes) and is allocated at its exact length. The reading is `build-tables` with `--cache-stats`, writing its memo files in line, whose `[c] <config> parked_heap` line gives the bytes the process's malloc zones hold allocated at the rendezvous, where every configuration's parked product and the parsed spec are all the process holds (`fixpoint::heap_bytes`, from `/usr/bin/heap`). A build that writes its memo files beside the wave can still be running the heaviest deltas' writers there, and MEMO_WRITE_OVERLAP_BYTES books what they add. Measured with ·Way in the alphabet on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), over every settlement configuration in the whole-wave readings DELTA_SLOT_BYTES cites, it reads 6.17 to 7.77 GB, the highest in the build reading previous memo files. The resident size and the footprint at the rendezvous are no measure of it: they also count the last enumerations' freed large allocations, which the allocator returns to the system when it chooses, so the `resident_parked` line beside it reads from 9.72 to 18.86 GB over the same runs. The constant is the highest reading divided by the settlement configuration count, the spec's share included, rounded up to the tenth. With the other terms as they stand, a value of 3.51 GB or more costs the 18-core machine its whole wave under a gated cycle. Re-measure it with DELTA_SLOT_BYTES whenever the alphabet grows or what a prepared product holds changes.
PARKED_FOLD_BYTES = 1_600_000_000
# The peak `table_build_booking_bytes` books for the one `build-tables` process at the configuration count with its memo files written beside the wave and the heaviest tier, `ss03` and `ss03+ss05`, started from scratch beside default: the width, the order and the count a bare run_m1 runs on both 48 GiB machines (`doc/fleet.md`), where a gated cycle runs the same width and order with no delta from scratch, which books less. It is DEFAULT_MEMO_BYTES, plus PARKED_FOLD_BYTES per configuration, plus SCRATCH_PEAK_BYTES less PARKED_FOLD_BYTES for each of those two deltas, plus DELTA_SLOT_BYTES for each of the other two, plus FOLD_PREPARATION_BYTES, plus MEMO_WRITE_OVERLAP_BYTES, a sum above the start's one SCRATCH_PEAK_BYTES for default and one for each of the two. `rebuild/test_memory_budget.py` checks that it equals that booking, so it moves whenever a term is re-measured. `make job-costs` compares every run_m1 step since its commit with it, and an overrun means a build outgrew what its width books, so the kernel constants need re-measuring. Its reading is the higher of two lines: the step's peak, which is the build process's resident line, and the build's peak footprint, which `build_table_files` reads between the process's exit and its reap and run_m1 writes on its `[t] kernel_build_tables` line. The resident line leaves out what the memory compressor holds of the build: in the readings SCRATCH_PEAK_BYTES cites, the build with every delta from scratch runs the machine's free pages down to their floor, and its resident line reads up to 5.48 GB below its footprint at 44 runes (8.93 GB in the 36.97 GB reading) and up to 12.77 GB below it at 45 runes. The row prints both lines, so an overrun the compressor's share makes can be told apart from a build that grew. A record without a footprint, which includes every build on Linux, where an exited process's footprint cannot be read, reads its resident line alone. Every whole-wave reading DELTA_SLOT_BYTES cites is inside it, the highest, 23.88 GB four wide, by 19.22 GB, and inside the booking at its own width by 3.04 GB or more. Every overlapped reading MEMO_WRITE_OVERLAP_BYTES cites is inside it, the highest, 20.48 GB five wide, by 22.62 GB, and inside the booking at its own width with that term by 7.75 GB or more. Every reading with deltas started from scratch beside default that SCRATCH_PEAK_BYTES cites is inside it, the highest, 39.36 GB with all four, by 3.74 GB, and with ·He in the alphabet the fresh five-wide build at the count it books, the heaviest tier, reads 22.34 and 22.95 GB, its footprint within 0.03 GB of its resident line (`var/keep/issue-519/`). Building a snapshot is inside this bound. `Engine::take_memo` releases the other live memos, then collects the trace map into an array, holding both during the collection, and drops the map before partitioning. Partitioning holds a four-byte source position per record and the offset cursors, plus a temporary sort buffer for an unusually large collision bucket, and frees the positions before it builds the tag blocks. `read_memo` reserves space from a count of the TSV's window lines, stores accepted records directly, and boxes the compacted array after indexing, with no growing hash table. Enumeration, the memo writer and the fold still hold corpus-sized state.
TABLE_BUILD_PEAK_BYTES = 43_100_000_000
# What the memo files' writers hold beside the table build when it writes them beside the delta wave and the folds (the crate's `--overlap-memo-writes`, `fanout::run_configs_tables`): the term `table_build_booking_bytes` adds with `overlap`, which `memo_writes_overlap` adds to the booking at the build's width to decide whether the build writes its memo files that way. Each writer holds its configuration's finished trace memo snapshot, one row per window it writes and the tables it interns until its last byte is written (`memo::write_memo`). Default's snapshot is the one the wave reads anyway (DEFAULT_MEMO_BYTES), but a delta's is otherwise freed before its drain, so a delta's writer keeps it through the drain, the sort and the fold. The reading is the whole-wave build DELTA_SLOT_BYTES cites run in both orders and paired width by width: one `build-tables` over every settlement configuration with memo output, `--timings` and `--cache-stats`, from scratch, under `/usr/bin/time -l`, once writing its memo files in line and once with `--overlap-memo-writes`, the two alternating, at five wide twice, four and three wide, two wide twice and one wide, and five wide reading the previous five-wide build's memo files with the newest letter edited, on an idle 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`). With ·Way in the alphabet the overlapped build's peak, the higher of its resident and footprint lines, reads 20.48 and 20.46 GB five wide, 19.71 GB four wide, 19.03 GB three wide, 16.68 and 16.95 GB two wide, 11.73 GB one wide and 18.82 GB five wide reading previous memos, from 4.17 GB below to 0.54 GB above its in-line twin. Every footprint line is 0.52 to 0.81 GB below its resident line. The writers show directly at the exchange's rendezvous, where four and five wide the two heaviest deltas' writers are still running: the `parked_heap` line reads 11.12 and 11.14 GB five wide, 10.93 GB four wide and 10.84 GB five wide reading previous memos, 3.07 to 4.98 GB above the in-line twin's, while three wide and narrower every writer has finished by then and it reads 0.14 to 0.85 GB below. The constant is the highest of those excesses, rounded up to the tenth. Booking it on top of every width errs high on purpose, for the reason DELTA_SLOT_BYTES gives: what the writers hold sits beside the parked products, after the deltas' peaks, and every overlapped reading is booked 7.75 GB or more above itself. The overlapped order took 184.1 and 184.2 s five wide against 209.8 and 215.8 s in line, 192.0 against 220.6 s four wide, 223.5 against 243.2 s three wide, 256.3 and 257.6 against 290.3 and 293.4 s two wide, 393.6 against 432.2 s one wide, and 149.6 against 181.3 s five wide reading previous memos. With the other terms as they stand, a value of 9.54 GB or more costs the 18-core machine the overlap under a gated cycle, 10.44 GB or more costs the 12-core machine the same, and 12.24 GB or more costs both machines the overlap alone; `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` check that both write their memo files beside the wave. Re-measure it with DELTA_SLOT_BYTES whenever the alphabet grows or what a writer holds changes, by the paired runs above.
MEMO_WRITE_OVERLAP_BYTES = 5_000_000_000
# The peak of one configuration's length-4 string replay; `run_m1.run_replay_strings` divides the machine's memory by it for its width. It covers the engine's trace memo over the windows its texts reach, with no liveness probes beyond the prospect's own and no ranking (`Replay::new` turns it off), plus the window memo's inverse label map and block buffer when the walk writes its dump. That is a subset of what the enumeration's engine holds, so it is a fraction of SCRATCH_PEAK_BYTES and measured separately. Nothing is subtracted before the division: the replay starts after the `build-tables` process has exited, so no configuration's memo is alive, and each worker loads one settlement TSV and builds its own engine. At 44 runes, with the trace key packed onto ordinals, the trace entry at sixteen bytes and no ranking, one `replay-strings` over `default` alone at `--threads=1` with `--memo-dir` on (the shipped path, since a full replay always writes its dump) peaks at 1.23 GB under `/usr/bin/time -l` on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), with its `peak memory footprint` line the same, and walks 1,926,220 texts over 3,256,994 windows in 8.6 s. Under `--cache-stats` its trace memo holds 6,144,028 entries in a table that holds 7,340,032 before it doubles, and its walk memo 3,256,994 in one that holds 3,670,016. Neither table has doubled since the same walk read 1.24 GB with ·Ye, which is why the peak is level with that reading, and the next doubling of either raises it. The same walk over every settlement configuration at the configuration count peaks at 6.07 GB for the whole process, 1.21 GB per worker, with no swaps, the footprint equal to the resident figure, and 0.33 s of system time against 48.9 s of user time. Each configuration's `[t] replay[<config>]` line reads 9.6 to 9.9 s against the solo 8.6 s. The five walks share the machine's memory without paging; paging would show in the system time and in a footprint above the resident figure, and neither rises here. The same divisor sizes the settle-memo absorbs that follow a full replay, since `run_m1.run_replay_strings` runs their pool at the replay's width. One absorb of `default`'s dump (`conform.absorb_replay_memo`, 3,256,994 rows) peaks at 0.29 GB in its own process, a fraction of the divisor. The logs are under `var/keep/issue-495/replay/`. The constant takes the higher of the solo reading and the wide run's per-worker figure, adds a quarter and rounds up to the tenth. It errs high for the reason DELTA_SLOT_BYTES gives, and the headroom also covers neighbors no term subtracts: the replay shares run_m1's process tree with the glyph chain, the window packers and the shipped-order walkers, each a small, flat working set beside it (`run_m1._core_bound_threads` sizes the last two). The criterion is that both fleet machines (`doc/fleet.md`) replay every settlement configuration in one round, alone and in a gated cycle with gate:make-test's pytest pool running; `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` check it. The replay-walk row of `make job-costs` checks it against each walk's share of the crate's peak (`run_m1._record_replay_walk`). Re-measure it whenever the alphabet grows, `REPLAY_MAX_LENGTH` changes or the trace memo's key or entry changes shape: the two runs above, both with the memo dump on, and the absorb over the solo run's dump. If a per-configuration line gets longer in the wide run while the system time rises, the width is causing paging.
REPLAY_PEAK_BYTES = 1_600_000_000


def table_build_booking_bytes(
    width: int,
    *,
    configs: int,
    from_scratch: bool = False,
    overlap: bool = False,
    scratch_beside_default: int = 0,
) -> int:
    """The memory a table build of `configs` configurations at `width` worker slots is booked at, which `kernel_threads_default` fits to the machine and `TABLE_BUILD_PEAK_BYTES` states at the configuration count. Every build books `DEFAULT_MEMO_BYTES` for `default`'s memo snapshots and `PARKED_FOLD_BYTES` for each configuration, whose prepared fold product stays parked until the cross-configuration exchange ends. A build that shares `default`'s memo books `DELTA_SLOT_BYTES` for each delta in flight, at most `configs - 1`, and above that delta count `FOLD_PREPARATION_BYTES` for the slot that runs only `default`'s fold preparation; a slot beyond the configuration count holds nothing. Both slot terms count only what a slot holds beyond its own configuration's parked product. With `overlap`, such a build also books `MEMO_WRITE_OVERLAP_BYTES` for its memo files' writers running beside the wave. With `scratch_beside_default`, capped as the crate caps it at the delta count and one less than `width`, that many of the deltas enumerate from scratch beside `default` instead (`fanout::run_configs_tables`), and each books `SCRATCH_PEAK_BYTES` less its parked product in place of a delta's slot. Such a build holds `default`'s live engine and theirs at their from-scratch peaks together before any delta starts, which the wave's terms do not cover, so its booking is the higher of that start, one `SCRATCH_PEAK_BYTES` for `default` and one for each of them, and the wave. With `from_scratch`, for a build in which every configuration enumerates from scratch (a set without `default`, or `default_memo_sharing=False`), each slot up to the configuration count books `SCRATCH_PEAK_BYTES` less its parked product instead, and neither `overlap` nor `scratch_beside_default` adds anything, because such a build writes each memo file ahead of its drain and has no `default` to start beside."""
    fixed = DEFAULT_MEMO_BYTES + configs * PARKED_FOLD_BYTES
    if from_scratch:
        return fixed + min(width, configs) * (SCRATCH_PEAK_BYTES - PARKED_FOLD_BYTES)
    deltas = configs - 1
    scratch = max(0, min(scratch_beside_default, deltas, width - 1))
    slots = (
        scratch * (SCRATCH_PEAK_BYTES - PARKED_FOLD_BYTES)
        + min(width - scratch, deltas - scratch) * DELTA_SLOT_BYTES
        + (FOLD_PREPARATION_BYTES if width > deltas else 0)
    )
    wave = fixed + slots + (MEMO_WRITE_OVERLAP_BYTES if overlap else 0)
    return max(wave, (1 + scratch) * SCRATCH_PEAK_BYTES) if scratch else wave


def kernel_threads_default(
    *,
    configs: int,
    coresident_bytes: float = 0,
    total_bytes: int | None = None,
    from_scratch: bool = False,
) -> int:
    """The number of table-build worker slots this machine has memory for, for a build of `configs` configurations: the widest width up to `configs` whose `table_build_booking_bytes` fits the machine's memory, less the reserve `memory_budget` states and less `coresident_bytes`, floored at one. `AMS_KERNEL_THREADS` overrides the arithmetic whenever it is set. Each slot runs one delta configuration, and `default`'s fold preparation takes one of them when the wave starts. So the width is `configs` when the whole wave fits, with `default`'s preparation in a slot of its own; otherwise it is `memory_budget.how_many_fit` over `DELTA_SLOT_BYTES`, with `DEFAULT_MEMO_BYTES` and a `PARKED_FOLD_BYTES` per configuration taken off first, capped at the delta count, where the preparation's slot passes to a delta once the preparation ends. `from_scratch` is for a build in which every configuration enumerates from scratch (a set without `default`, which `run_m1.build_tables` passes it for, or `default_memo_sharing=False`): its divisor is `SCRATCH_PEAK_BYTES` less `PARKED_FOLD_BYTES`, capped at `configs`. A stated `AMS_KERNEL_THREADS` below the configuration count still parks every configuration's product, so a width of one does not bound the peak near one delta.

    A stated width is floored at one and not capped here, because the configuration count and the usable cores are not memory facts. `run_m1._table_build_threads` and the cycle's `artifact_cycle.kernel_threads_budget` apply those caps; this module cannot name `conform.SETTLEMENT_CONFIGS`, since `conform` imports it. A value that is not a bare count (a typo, a `GB` suffix, an empty variable) raises instead of falling back to the arithmetic. `AMS_TOTAL_MEMORY_BYTES` ignores such a value, because it only reproduces another machine. This variable is what someone sets to keep a build out of swap, and a width they did not ask for defeats that. `total_bytes` is a keyword so a test can compute the width for an invented machine. The alternative, `importlib.reload`, re-runs module scope, resets `_BUILT` and deletes the live `_SPEC_DUMPS` directories under a caller still holding a `spec_path`. `configs` comes from the caller for the same reason, so no width is computed at import.

    `coresident_bytes` is for a caller that runs the fan-out beside something else: the artifact cycle, whose pytest pool runs for the whole table build. Those bytes come off the machine with the reserve, so the width fits the machine the fan-out will run on. `AMS_KERNEL_THREADS` still overrides it, because nothing derived here may narrow a stated width. A bare `run_m1` has nothing beside it and passes `0`. Callers use this function instead of calling `how_many_fit` themselves, which would duplicate the override branch.
    """
    stated = os.environ.get("AMS_KERNEL_THREADS")
    if stated is not None:
        try:
            return max(1, int(stated))
        except ValueError:
            raise RuntimeError(
                f"AMS_KERNEL_THREADS={stated!r} is not a width: it takes a bare decimal count of configurations to hold in flight, and leaving it unset is what asks for the width this machine's own memory derives."
            ) from None
    total = memory_budget.total_memory_bytes() if total_bytes is None else int(total_bytes)
    usable = total - memory_budget.os_reserve_bytes(total_bytes=total) - max(0, int(coresident_bytes))
    if not from_scratch and table_build_booking_bytes(configs, configs=configs) <= usable:
        return configs
    per_slot, cap = (
        (SCRATCH_PEAK_BYTES - PARKED_FOLD_BYTES, configs) if from_scratch else (DELTA_SLOT_BYTES, configs - 1)
    )
    return memory_budget.how_many_fit(
        per_slot,
        coresident_bytes=DEFAULT_MEMO_BYTES + configs * PARKED_FOLD_BYTES + coresident_bytes,
        cap=cap,
        total_bytes=total,
    )


def memo_writes_overlap(
    width: int,
    *,
    configs: int,
    coresident_bytes: float = 0,
    total_bytes: int | None = None,
    from_scratch: bool = False,
) -> bool:
    """Whether a table build of `configs` configurations at `width` worker slots writes its memo files beside the delta wave and the folds, the `overlap_memo_writes` `run_m1.build_tables` passes `build_table_files`: true when the build shares `default`'s memo and its booking at that width with `MEMO_WRITE_OVERLAP_BYTES` added (`table_build_booking_bytes` with `overlap`) fits the machine's memory, less the reserve `memory_budget` states and less `coresident_bytes`. At the width `kernel_threads_default` derives from the same memory, that holds exactly when taking the term off the memory first would leave that width unchanged, except where taking it off floors that width at one; so a build whose width memory sets keeps each memo write ahead of the work that follows it, which costs time and no memory, unless what that width leaves over holds the term. A width that is stated rather than derived (`AMS_KERNEL_THREADS`, `--kernel-threads`) is judged at that width the same way, so a width stated past what memory books never has the writers added on top. A from-scratch build writes each file ahead of its drain whatever this says, so it answers false. `coresident_bytes` and `total_bytes` mean what they mean for `kernel_threads_default`."""
    if from_scratch:
        return False
    total = memory_budget.total_memory_bytes() if total_bytes is None else int(total_bytes)
    usable = total - memory_budget.os_reserve_bytes(total_bytes=total) - max(0, int(coresident_bytes))
    return table_build_booking_bytes(width, configs=configs, overlap=True) <= usable


def deltas_from_scratch(
    width: int,
    *,
    configs: int,
    overlap: bool,
    coresident_bytes: float = 0,
    total_bytes: int | None = None,
    from_scratch: bool = False,
) -> int:
    """At most how many of the deltas a table build of `configs` configurations at `width` worker slots enumerates from scratch beside `default`, the `scratch_beside_default` `run_m1.build_tables` passes `build_table_files`. The crate takes them heaviest first (`fanout::delta_worklist`), so the count is all this decides: deltas are taken in that order while the build's booking (`table_build_booking_bytes` with `overlap`, the choice `memo_writes_overlap` made at the same width) with one more of them still fits the machine's memory, less the reserve `memory_budget` states and less `coresident_bytes`, up to the delta count and one less than `width`. The crate starts them only in whole tiers of equal unlocking-rune count up to that bound (`fanout::scratch_tiers`), because a delta left tied with one taken still runs after `default` as long as the one taken would have, while `default` enumerates more slowly beside them; where it starts fewer, the booking at this count errs high. The width and the memo-write order are decided first and never narrowed by this, so a machine whose width memory sets runs every delta over `default`'s memo unless what that width leaves over holds the from-scratch peaks; the count only costs memory and CPU, since every configuration enumerated from scratch traces all of its windows. A stated width is judged the same way. A from-scratch build has nothing to start beside `default`, so it answers zero. `coresident_bytes` and `total_bytes` mean what they mean for `kernel_threads_default`."""
    if from_scratch:
        return 0
    total = memory_budget.total_memory_bytes() if total_bytes is None else int(total_bytes)
    usable = total - memory_budget.os_reserve_bytes(total_bytes=total) - max(0, int(coresident_bytes))
    count = 0
    while (
        count < min(configs - 1, width - 1)
        and table_build_booking_bytes(
            width, configs=configs, overlap=overlap, scratch_beside_default=count + 1
        )
        <= usable
    ):
        count += 1
    return count


def describe_kernel_threads(*, configs: int, total_bytes: int | None = None) -> str:
    """`kernel_threads_default`'s arithmetic for a build of `configs` configurations that shares `default`'s memo, with nothing beside it, as one clause for `run_m1 --help`, so a reader can check where the derived width comes from. When the whole wave fits, the clause gives the booking at the configuration count; otherwise it is `memory_budget.describe_fit` over the terms `kernel_threads_default` divides. `AMS_KERNEL_THREADS` is left out, because the help names it beside the clause."""
    total = memory_budget.total_memory_bytes() if total_bytes is None else int(total_bytes)
    reserve = memory_budget.os_reserve_bytes(total_bytes=total)
    booked = table_build_booking_bytes(configs, configs=configs)
    if booked <= total - reserve:
        return f"{configs}, the whole wave, booked at {format_gb(booked)} GB out of {format_gb(total)} GB total, less a reserve of {format_gb(reserve)} GB"
    return memory_budget.describe_fit(
        DELTA_SLOT_BYTES,
        coresident_bytes=DEFAULT_MEMO_BYTES + configs * PARKED_FOLD_BYTES,
        cap=configs - 1,
        total_bytes=total,
    )


def replay_threads_default(*, coresident_bytes: float = 0, total_bytes: int | None = None) -> int:
    """The number of settlement configurations the string replay's one crate process walks at once, as far as memory decides. `AMS_REPLAY_THREADS` overrides the arithmetic whenever it is set. Otherwise `memory_budget.how_many_fit` divides the machine's memory, less the reserve that policy states, by `REPLAY_PEAK_BYTES`, floored at one. Nothing else is subtracted: a replay loads one settlement TSV and builds its own engine after the `build-tables` process has exited, so no `default` memo is alive. The configuration-count and usable-core caps are applied by `run_m1._replay_threads` and the cycle's `artifact_cycle.replay_threads_budget`, because this module cannot name `conform.SETTLEMENT_CONFIGS`, since `conform` imports it. A stated width is floored at one and not capped here, and a value that is not a bare count raises, for the same reason as with `AMS_KERNEL_THREADS`.

    `coresident_bytes` is for a caller that runs the replay beside something else: the artifact cycle, whose pytest pool runs for the whole run_m1 step. `total_bytes` is a keyword so a test can compute the width for an invented machine. On every fleet machine (`doc/fleet.md`) the configuration count is smaller than the memory answer, with or without the pool subtracted, so the memory term only matters on a smaller machine.
    """
    stated = os.environ.get("AMS_REPLAY_THREADS")
    if stated is not None:
        try:
            return max(1, int(stated))
        except ValueError:
            raise RuntimeError(
                f"AMS_REPLAY_THREADS={stated!r} is not a width: it takes a bare decimal count of settlement configurations to replay at once, and leaving it unset is what asks for the width this machine's own memory derives."
            ) from None
    return memory_budget.how_many_fit(
        REPLAY_PEAK_BYTES, coresident_bytes=coresident_bytes, total_bytes=total_bytes
    )


# How long a crate call that answers in seconds or minutes at any alphabet size is waited for before `_run_kernel` kills it: `enumerate-configs`, which only the tests run, `settle-cases`, `guard-sweep`, `replay-emitted`, and `cargo build`. A limit is there to stop a crate that has hung, not to bound a slow one, and a call it kills records nothing. So `build-tables` and `replay-strings`, whose time grows with the alphabet, derive their limits from it at the call (`build_tables_timeout`, `replay_timeout`), and neither waits less than this.
TIMEOUT = 1800
# The factor between the duration `build_tables_timeout` or `replay_timeout` scales from a measurement and the limit it returns. It covers what the scaling leaves out: the 12-core M4 Pro Mac mini (`doc/fleet.md`), which has no measured duration; a call that shares its cores with another pool; and for the replay, the growth in what a text costs as the tables grow, which the full-alphabet projection puts at up to 1.3 times. A limit set too low kills a healthy call and loses its whole work, while one set too high only delays the error a hung crate gets, so the margin errs high.
KERNEL_TIMEOUT_MARGIN = 3
# The alphabet the two measured durations below were taken over: the 44-rune alphabet's 34 letters and three boundary tokens (`labels.spec_alphabet`).
MEASURED_SYMBOLS = 37
# The slowest whole-set table build at MEASURED_SYMBOLS: one `build-tables` over every settlement configuration one wide, from scratch with memo output, 432.2 s on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), the slowest of the whole-wave readings DELTA_SLOT_BYTES cites. Every wider build of the same alphabet took less.
BUILD_TABLES_SECONDS = 432.2
# How the table build's time grows with the alphabet: as the symbol count to this power, the number of slots in a window (its input, its left and four rights). It gives the 35th, 36th and 37th symbols 19.0%, 18.4% and 17.9% more time, against the 16.4%, 15.5–16.3% and 18.8% that fresh builds of the same code grew by when ·Jay, ·Ye and ·Way were added, and from 37 symbols to the full alphabet's 47 it gives 4.2 times, above the 3.6 to 3.8 times that is the pessimistic projection of that growth.
BUILD_TABLES_GROWTH = 6
# What one text costs the replay at MEASURED_SYMBOLS: the slowest of five walks over every text at maximum length 5, one configuration each, run at once in one `replay-strings` process at the deep replay's memo ceiling on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), 71,270,177 texts in 542.3 s (`var/keep/issue-495/replay/replay-deep-all.log`). The build's own replay at maximum length 4 costs less a text: 9.9 s for 1,926,220 texts at most, in REPLAY_PEAK_BYTES's wide run.
REPLAY_TEXT_SECONDS = 542.3 / 71_270_177
# On macOS, where cargo copies the binary into target/release instead of hard-linking it, every `cargo build` replaces it there (it removes the file, then copies the new one in) even when nothing recompiled, so a build in one process can make another process's exec miss the file for an instant. This lock orders the two: a build holds it exclusively for the whole `cargo build`, and an invocation holds it shared for the spawn only, never for the run.
LOCK_PATH = MANIFEST.parent / "target" / ".ams-kernel-relink.lock"
# How many lines of a failed build's stderr the exception includes: cargo reports the error in its last few lines, after the full compilation log.
BUILD_TAIL_LINES = 20
# On by default: the third join-count term is scored by the follower's simulated transition instead of junction-bearing candidacy. `AMS_SIMULATED_PROSPECT=0` turns it off for a comparison run, and run_m1's spawn-pool workers inherit that through the environment. It is read at call time, so a test may monkeypatch it; a caller that wants one named mode set regardless passes `SettlementModes`.
SIMULATED_PROSPECT_DEFAULT = os.environ.get("AMS_SIMULATED_PROSPECT", "1") != "0"
# On by default: follower prefers are evaluated over the settled position's real shifted slots (follower prefer right1 = position right2, right2 = position right3, right3 = position right4) instead of pinning every slot past the follower prefer's own right1 to UNKNOWN, so a chained follower prefer resolves inside the window instead of firing optimistically wherever its then: hop read the pin. `AMS_FOLLOWER_PREFER_SLOTS=0` is the comparison state. It is a module attribute read at call time, like SIMULATED_PROSPECT_DEFAULT.
FOLLOWER_PREFER_SLOTS_DEFAULT = os.environ.get("AMS_FOLLOWER_PREFER_SLOTS", "1") != "0"
# On by default: deep window slots are enumerated at class grain, one row per outcome fiber, expanded back to labels for every fold-side consumer. It is a kernel invocation flag passed by `mode_flags` like the two defaults above, read at call time, with `AMS_DEEP_CLASSES=0` the label-grain comparison state. `class_grain` states the grain rule the crate applies.
DEEP_CLASSES_DEFAULT = os.environ.get("AMS_DEEP_CLASSES", "1") != "0"
# The semantics flags a fixpoint's shape depends on, each as (the kernel flag that turns it off, the module holding the default, the attribute name). The module is named instead of closed over so a later call reads a monkeypatched attribute. Only a flag that is off appears on the command line, so the default modes invoke the subcommand with none.
SETTLEMENT_FLAGS = (
    ("--candidacy-prospect", sys.modules[__name__], "SIMULATED_PROSPECT_DEFAULT"),
    ("--follower-prefer-slots-off", sys.modules[__name__], "FOLLOWER_PREFER_SLOTS_DEFAULT"),
)
MODE_FLAGS = (
    *SETTLEMENT_FLAGS,
    ("--deep-classes-off", sys.modules[__name__], "DEEP_CLASSES_DEFAULT"),
)
GUARD_TAIL_TOKENS = {
    token.kind: token for token in (settle.EDGE, settle.SPACE, settle.ZWNJ, settle.NAMER_DOT, settle.UNKNOWN)
}
FormationGuard = settle.FormationGuard
# How many windows one `settle-cases` invocation takes on the sequence path, where every case result is a whole decoded trace. A spawn with its spec load costs about nine milliseconds and a case about fifteen microseconds to settle and serialize, so at this size the spawn is under a quarter of a batch's kernel time, while the decoded traces, each under a kilobyte of text, stay bounded.
SETTLE_CASE_BATCH_SIZE = 2048
# The same bound for `settle_windows`, where a case result decodes to a `Settled` only. It is eight times the trace batch because a settled record costs a fraction of a trace's memory. At this size the spawn is under a twentieth of a batch's kernel time, so a larger batch gains nothing measurable (issue #153 measured it).
SETTLE_WINDOW_BATCH = 16384

_BUILT = False
# The marker on a memo file's head line (`rebuild/kernel-rs/src/memo.rs`'s `MEMO_FORMAT`); `read_memo_head` rejects a file with any other marker.
MEMO_FORMAT = "ams-m1-memo/1"
# The stamp for a window enumeration that is not kept. `build-tables` always writes the windows payload, whose head is where a caller reads the rules and cells back from, and always requires a stamp for that head. A caller with no fingerprint over the rune files has no stamp to give, so it passes this word and deletes the payload after reading it instead of packing it, and the word never reaches an artifact.
UNSTAMPED_WINDOWS = "unstamped"

# One scratch dump per live spec, keyed on identity. Each entry holds the spec itself so its id cannot be reused while the entry exists. A dump costs about ten milliseconds and a couple of hundred kilobytes, which the settlement functions would otherwise pay on every invocation. The cap is small because the callers that matter hold one spec, and eviction deletes the directory.
_SPEC_DUMPS: OrderedDict[int, tuple[ResolvedSpec, tempfile.TemporaryDirectory, Path]] = OrderedDict()
_SPEC_DUMPS_CAP = 4
_SPEC_DUMPS_LOCK = threading.Lock()

# The same structure for the guard verdicts, which cost a crate invocation instead of a file write.
_GUARD_SWEEPS: OrderedDict[int, tuple[ResolvedSpec, FormationGuard]] = OrderedDict()
_GUARD_SWEEPS_CAP = 4
_GUARD_SWEEPS_LOCK = threading.Lock()


def build_tables_timeout(symbols: int) -> float:
    """How long one `build-tables` call over an alphabet of `symbols` symbols is waited for: `KERNEL_TIMEOUT_MARGIN` times `BUILD_TABLES_SECONDS` scaled by `symbols` over `MEASURED_SYMBOLS` to the `BUILD_TABLES_GROWTH` power, and at least `TIMEOUT`. It takes no width or configuration count, because the duration it scales is the whole set one wide, the slowest form the build runs in."""
    scaled = BUILD_TABLES_SECONDS * (symbols / MEASURED_SYMBOLS) ** BUILD_TABLES_GROWTH
    return max(float(TIMEOUT), KERNEL_TIMEOUT_MARGIN * scaled)


def replay_timeout(*, symbols: int, max_length: int, configs: int, threads: int, last: bool) -> float:
    """How long one `replay-strings` call is waited for: `KERNEL_TIMEOUT_MARGIN` times what its walks take at `REPLAY_TEXT_SECONDS` a text, and at least `TIMEOUT`. A walk covers every text of length 1 to `max_length` over `symbols` symbols, or with `last` the texts ending in one of them; a family list only narrows that, so the limit counts every text. A call's workers each walk their configurations one after another, so it takes as many walks' time as the most configurations one worker walks: `configs` over the width, rounded up, where the width is the smallest of `threads`, `configs` and the usable cores, as the crate caps it."""
    texts = sum(symbols ** (length - 1 if last else length) for length in range(1, max_length + 1))
    width = max(1, min(threads, configs, memory_budget.usable_cores()))
    rounds = -(-configs // width)
    return max(float(TIMEOUT), KERNEL_TIMEOUT_MARGIN * REPLAY_TEXT_SECONDS * texts * rounds)


class KernelBuildError(RuntimeError):
    """`cargo` is absent or the crate did not build. Distinct from a run failure, which is a binary that exists and answered badly."""


class KernelRunError(RuntimeError):
    """The binary refused the invocation, exited nonzero, reported an error on a clean exit, or left a stream unwritten."""


def cargo_build() -> None:
    """Build the kernel in release mode, as `make kernel-build` does, holding the binary relink lock exclusively. Checking that the binary exists is not enough, because a stale binary sits at the same path as a fresh one; building makes sure the sources on disk are what runs. A warm build takes a fraction of a second; `ensure_built` runs it once per process."""
    arguments = ["cargo", "build", "--release", "--manifest-path", str(MANIFEST)]
    try:
        with _relink_lock(fcntl.LOCK_EX):
            finished = subprocess.run(arguments, capture_output=True, timeout=TIMEOUT)
    except FileNotFoundError:
        raise KernelBuildError(
            "no cargo on PATH — install the Rust toolchain (https://rustup.rs) to build the M1 kernel"
        ) from None
    except subprocess.TimeoutExpired:
        raise KernelBuildError(
            f"cargo gave no answer within {TIMEOUT} seconds on {' '.join(arguments)}"
        ) from None
    if finished.returncode != 0:
        errors = finished.stderr.decode(errors="replace").strip().split("\n")
        tail = "\n".join(errors[-BUILD_TAIL_LINES:])
        raise KernelBuildError(f"the kernel did not build (cargo exited {finished.returncode}):\n{tail}")


class _RelinkLock:
    def __init__(self, mode: int) -> None:
        self._mode = mode
        self._handle = None

    def __enter__(self):
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._handle = LOCK_PATH.open("w")
        fcntl.flock(self._handle, self._mode)
        return self

    def __exit__(self, *_exc) -> None:
        assert self._handle is not None
        fcntl.flock(self._handle, fcntl.LOCK_UN)
        self._handle.close()


def _relink_lock(mode: int) -> _RelinkLock:
    return _RelinkLock(mode)


def _spawn_kernel(arguments: list[str], *, stdin: bool) -> subprocess.Popen:
    """Spawn the binary with the relink lock held shared for the spawn only, the moment a concurrent `cargo build` could make the path disappear, so the caller waits without the lock and a long enumeration never blocks a build elsewhere. With `stdin`, the child's stdin is a pipe; without it, the child inherits this process's stdin. Raises `KernelRunError` for a missing binary."""
    try:
        with _relink_lock(fcntl.LOCK_SH):
            return subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE if stdin else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
    except FileNotFoundError:
        raise KernelRunError(
            f"no kernel binary at {BINARY} — run `make kernel-build` first, or let the caller's cargo_build() build it"
        ) from None


def _no_answer(arguments: list[str], verb: str, timeout: float) -> KernelRunError:
    return KernelRunError(
        f"the kernel gave no answer within {timeout:.0f} seconds on {verb} ({' '.join(arguments)})"
    )


def _run_kernel(
    arguments: list[str], verb: str, stdin: bytes | None = None, *, timeout: float
) -> subprocess.CompletedProcess:
    """Run the binary to completion (`_spawn_kernel`) and return its exit code and output, waiting at most `timeout` seconds, the limit the caller states for its verb. With `stdin`, the child's stdin carries those bytes and is then closed. Raises `KernelRunError` for a missing binary or a call that has not answered within `timeout`, after killing it."""
    process = _spawn_kernel(arguments, stdin=stdin is not None)
    try:
        stdout, stderr = process.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise _no_answer(arguments, verb, timeout) from None
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def _run_kernel_reaped(
    arguments: list[str], verb: str, *, timeout: float
) -> tuple[subprocess.CompletedProcess, ReapedPeaks]:
    """`_run_kernel` with no stdin, returning beside the result the child process's own resident peak and peak footprint in bytes (`peak_rss.reap_peaks_bytes`), either None where it cannot be read. `communicate` reaps the child with `waitpid`, which discards its resource usage and its footprint, so this drains each pipe on a thread of its own and, once both are closed, waits for the child's exit, reads its peak footprint and reaps it with `os.wait4`. The crate starts no process of its own, so both figures are the crate's. Raises `KernelRunError` for a missing binary, or for a timeout after killing the child."""
    process = _spawn_kernel(arguments, stdin=False)
    assert process.stdout is not None and process.stderr is not None
    drained: dict[str, bytes] = {}

    def drain(name: str, stream: IO[bytes]) -> None:
        with stream:
            drained[name] = stream.read()

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    for reader in readers:
        reader.join(max(0.0, deadline - time.monotonic()))
    if any(reader.is_alive() for reader in readers):
        process.kill()
        for reader in readers:
            reader.join()
        process.wait()
        raise _no_answer(arguments, verb, timeout)
    peaks = reap_peaks_bytes(process)
    returncode = process.wait()
    return subprocess.CompletedProcess(arguments, returncode, drained["stdout"], drained["stderr"]), peaks


def ensure_built() -> None:
    """Run `cargo_build` once per process and do nothing on later calls. A warm `cargo` still costs a fraction of a second, and a suite or cycle stage that builds a hundred tables would pay it a hundred times for a binary that cannot have changed. A caller that wants cargo consulted again calls `cargo_build` directly."""
    global _BUILT
    if _BUILT:
        return
    cargo_build()
    _BUILT = True


def mode_flags() -> list[str]:
    """The mode flags that make the kernel enumerate under this process's mode set: one per default that is off. The defaults are read at call time. `run_m1.tables_inputs` stamps the same three settings through `enumeration_tokens`, so an enumeration made with a flag on is never mistaken for one made with it off."""
    return [flag for flag, module, attribute in MODE_FLAGS if not getattr(module, attribute)]


@dataclass(frozen=True)
class SettlementModes:
    """One named settlement mode set, for a caller that wants a mode set other than its process's. `current()` returns the process's own, read from the module defaults at call time. `flags()` returns the command-line flags for the two booleans, written and ordered by `SETTLEMENT_FLAGS`. Passing an explicit pair lets a caller, such as a test, choose a mode set without changing the module defaults every other caller in the process reads."""

    simulated_prospect: bool
    follower_prefer_slots: bool

    @classmethod
    def current(cls) -> SettlementModes:
        return cls(
            simulated_prospect=SIMULATED_PROSPECT_DEFAULT, follower_prefer_slots=FOLLOWER_PREFER_SLOTS_DEFAULT
        )

    def flags(self) -> list[str]:
        on = {
            "SIMULATED_PROSPECT_DEFAULT": self.simulated_prospect,
            "FOLLOWER_PREFER_SLOTS_DEFAULT": self.follower_prefer_slots,
        }
        return [flag for flag, _module, attribute in SETTLEMENT_FLAGS if not on[attribute]]


def settlement_flags(modes: SettlementModes | None = None) -> list[str]:
    """The two mode flags every direct settlement invocation takes, for `modes` or for this process's mode set. Deep-class grain applies only to enumeration, so it is not in this list."""
    if modes is None:
        modes = SettlementModes.current()
    return modes.flags()


def class_grain() -> bool:
    """Whether this process's enumeration splits deep slots into outcome fibers, restating the crate's grain rule for Python callers. `AMS_DEEP_CLASSES` asks for class grain, but fibers exist only where a deep token can change an outcome. In the pinned mode set, with neither the simulated prospect nor the shifted follower prefer slots, nothing can, and the crate enumerates at label grain whatever the flag says. `enumeration_tokens` reads this, because the stamp on a serialized enumeration must distinguish the two grains."""
    return DEEP_CLASSES_DEFAULT and (SIMULATED_PROSPECT_DEFAULT or FOLLOWER_PREFER_SLOTS_DEFAULT)


def enumeration_tokens() -> list[str]:
    """The semantics tokens a stamp over this process's enumeration must include, in stamp order: the simulated prospect, the shifted follower prefer slots and the class-grain deep slots, each present only while it is on. Each changes settlement semantics or enumeration grain without changing a hashed source, so a key over the sources alone would treat an enumeration made with a flag on as current in a process with it off, and the reverse. `run_m1.tables_inputs` appends these to the tables' stamp, `run_m1.previous_memos` joins them into the memo head's mode set, and `run_m1.locality_lines` includes them in the memo stamp, so the three agree."""
    tokens: list[str] = []
    if SIMULATED_PROSPECT_DEFAULT:
        tokens.append("simulated-prospect")
    if FOLLOWER_PREFER_SLOTS_DEFAULT:
        tokens.append("follower-prefer-slots")
    if class_grain():
        tokens.append("deep-classes")
    return tokens


def _spec_dump(spec: ResolvedSpec) -> Path:
    """The path of this spec's `spec.json`, dumped once per spec identity per process. Every settlement function and the guard sweep read the same file, so a walker that settles a hundred batches of one spec writes it once. The entry holds the spec so its `id` cannot be reused while the dump stands for it, and holds the `TemporaryDirectory` so eviction removes the file instead of garbage collection."""
    key = id(spec)
    with _SPEC_DUMPS_LOCK:
        held = _SPEC_DUMPS.get(key)
        if held is not None and held[0] is spec:
            _SPEC_DUMPS.move_to_end(key)
            return held[2]
        scratch = tempfile.TemporaryDirectory()
        path = Path(scratch.name) / "spec.json"
        kernel_io.write_spec(spec, path)
        _SPEC_DUMPS[key] = (spec, scratch, path)
        _SPEC_DUMPS.move_to_end(key)
        while len(_SPEC_DUMPS) > _SPEC_DUMPS_CAP:
            _evicted_key, (_evicted_spec, evicted_scratch, _evicted_path) = _SPEC_DUMPS.popitem(last=False)
            evicted_scratch.cleanup()
        return path


def enumerate_configs(
    spec_path: Path,
    out_dir: Path,
    configs: Sequence[str],
    *,
    threads: int,
    timings: bool = False,
    timings_tag: str | None = None,
) -> dict[str, Path]:
    """Every named configuration's transition stream, enumerated by one kernel process into `out_dir` and returned as `{config: path}`. The streams are byte-identical to what the same binary writes one configuration at a time, at any thread width. The files are plain ndjson, since compression is Python's job and the crate depends only on serde_json. The caller's configuration token names each file, because the crate rejects a token that is not the canonical form of the features it names. Raises `KernelRunError` for every kind of failure the CLI contract distinguishes, and for a clean exit that left a stream unwritten.

    `timings_tag` names the configuration a whole invocation stands for, for a caller that runs one process per configuration. The crate already labels its per-configuration lines `enumerate[<config>]`, but `spec_parse` and `enumerate_total` name the process, so without a tag each process's pair could not be attributed in the cycle journal. Tagged, they read `spec_parse[<config>]`.
    """
    arguments = [
        str(BINARY),
        "enumerate-configs",
        str(spec_path),
        str(out_dir),
        f"--configs={','.join(configs)}",
        f"--threads={threads}",
        *mode_flags(),
    ]
    if timings:
        arguments.append("--timings")
    finished = _run_kernel(arguments, "enumerate-configs", timeout=TIMEOUT)
    errors = finished.stderr.decode(errors="replace").strip()
    if finished.returncode == 2:
        raise KernelRunError(
            f"kernel does not support enumerate-configs yet, or rejected the invocation as a usage error: {errors} ({' '.join(arguments)})"
        )
    if finished.returncode != 0:
        raise KernelRunError(f"the kernel exited {finished.returncode} on enumerate-configs: {errors}")
    if finished.stdout:
        raise KernelRunError(
            f"the kernel wrote {len(finished.stdout)} bytes to stdout on a clean enumerate-configs exit, where the answer is the files"
        )
    _forward_stderr(errors, timings, arguments, timings_tag)
    streams = {config: out_dir / f"transitions-{config}.ndjson" for config in configs}
    missing = [config for config, path in streams.items() if not path.is_file()]
    if missing:
        left = sorted(found.name for found in out_dir.glob("*")) if out_dir.is_dir() else []
        raise KernelRunError(
            f"the kernel exited clean but wrote no stream for {', '.join(missing)} — it left {left}"
        )
    return streams


@dataclass(frozen=True)
class TableBuild:
    """What `build_table_files` returns: each configuration's table digest, keyed by configuration, the `[t]` lines the crate wrote under `--timings`, as `_forward_stderr` copied them to stderr, and the `build-tables` process's own resident peak and peak footprint (`_run_kernel_reaped`). `run_m1.build_tables` keeps the lines for run_m1's check line, which has no captured output to parse them from, and writes the two peaks on its `[t] kernel_build_tables` line."""

    digests: dict[str, str]
    timings: tuple[str, ...]
    peaks: ReapedPeaks


def build_table_files(
    spec_path: Path,
    out_dir: Path,
    configs: Sequence[str],
    *,
    inputs: str,
    threads: int,
    symbols: int,
    timings: bool = False,
    timings_tag: str | None = None,
    default_memo_sharing: bool = True,
    previous_memos: Path | None = None,
    edited: Sequence[str] = (),
    moved_classes: Sequence[str] = (),
    memo_stamp: str | None = None,
    overlap_memo_writes: bool = False,
    scratch_beside_default: int = 0,
    verdict_sharing: bool = True,
) -> TableBuild:
    """Every named configuration folded in the crate: its settlement TSV, its join TSV, its plain window enumeration stamped `inputs`, and the table digest of the pair, returned as `TableBuild.digests` (`{config: digest}`) beside the `[t]` lines `--timings` made the crate write.

    This is `enumerate-configs` plus the fold in one process, so there is no stream between them: the fold runs on the product the worklist still holds. The windows payload is written uncompressed because the crate depends only on serde_json, and `run_m1.build_tables` packs it into the `.gz` artifact.

    One process handles every named configuration because the configurations after `default` are enumerated as deltas over it. `default` enumerates first and keeps its trace memo. Each other configuration reads that memo for every window whose key names none of its own unlocking runes and whose settlement read none of them, and settles only the rest, `threads` worker slots at a time, with `default`'s fold preparation in one of them. `rebuild/kernel-rs/src/memo.rs` gives the argument; the window locality rule, applied across configurations, makes the shared results exact. `default_memo_sharing=False` enumerates every configuration from scratch, and `rebuild/test_kernel_exec.py` checks that it writes the same tables as the delta build. `scratch_beside_default` starts up to that many of the heaviest deltas from scratch beside `default` instead, in whole tiers of equal unlocking-rune count, which writes the same tables and holds what `table_build_booking_bytes` books for it (`deltas_from_scratch` decides it); a configuration enumerated from scratch memoizes every window it settles, so its memo file holds the windows a delta's leaves to `default`'s. When some delta reads `default`'s memo, `default` also publishes its deep-slot liveness verdicts beside it, and a delta served one reruns only the probe's asks that name one of its own unlocking runes (`rebuild/kernel-rs/src/liveness.rs`); `verdict_sharing=False` runs every probe whole, and `rebuild/test_kernel_exec.py` checks that it writes the same tables and memo files.

    The memo is also carried across builds. `previous_memos` is a directory of a previous build's plain `memo-<config>.tsv` files. They are read with `edited` (the runes whose content changed since) and `moved_classes` (the predicate classes whose membership changed) excluded, so a window whose settlement read none of them settles as it did then. The crate's read journal records what each entry read (`rebuild/kernel-rs/src/index.rs`). `memo_stamp` is the stamp this build writes its own plain `memo-<config>.tsv` files under, beside the tables; `run_m1.build_tables` packs them into `.gz` artifacts. `overlap_memo_writes` writes each of those files on a thread of its own beside the delta wave and the folds instead of ahead of them, which writes the same bytes and holds what `MEMO_WRITE_OVERLAP_BYTES` books (`memo_writes_overlap` decides it). Python decides which files may be read and which runes count as edited (`run_m1.previous_memos`), from the stamp in each head; the crate checks only that a file matches its configuration and mode set.

    `symbols` is the size of the spec's alphabet (`labels.spec_alphabet`), which sets how long the call is waited for (`build_tables_timeout`).

    The process runs through `_run_kernel_reaped`, which reads its peak footprint after it exits and before it is reaped, so `TableBuild.peaks` carries both its resident peak and its peak footprint. The resident line leaves out what the memory compressor holds of the build, and the kernel-build row of `make job-costs` reads the higher of the two (`TABLE_BUILD_PEAK_BYTES`).

    The digests are returned on stdout, one JSON object per line in the order the configurations were named, because a digest is a value the caller keeps and reports, not a separate artifact. Raises `KernelRunError` for every kind of failure the CLI contract distinguishes, and for a clean exit whose output names a different set of configurations from the one requested.
    """
    arguments = [
        str(BINARY),
        "build-tables",
        str(spec_path),
        str(out_dir),
        f"--configs={','.join(configs)}",
        f"--inputs={inputs}",
        f"--threads={threads}",
        *mode_flags(),
    ]
    if not default_memo_sharing:
        arguments.append("--no-default-memo-sharing")
    if not verdict_sharing:
        arguments.append("--no-verdict-sharing")
    if previous_memos is not None:
        arguments.append(f"--previous-memos={previous_memos}")
        if edited:
            arguments.append(f"--edited={','.join(edited)}")
        if moved_classes:
            arguments.append(f"--moved-classes={','.join(moved_classes)}")
    if memo_stamp is not None:
        arguments.append(f"--memo-stamp={memo_stamp}")
    if overlap_memo_writes:
        arguments.append("--overlap-memo-writes")
    if scratch_beside_default:
        arguments.append(f"--scratch-beside-default={scratch_beside_default}")
    if timings:
        arguments.append("--timings")
    finished, peaks = _run_kernel_reaped(arguments, "build-tables", timeout=build_tables_timeout(symbols))
    errors = finished.stderr.decode(errors="replace").strip()
    if finished.returncode == 2:
        raise KernelRunError(
            f"kernel does not support build-tables yet, or rejected the invocation as a usage error: {errors} ({' '.join(arguments)})"
        )
    if finished.returncode != 0:
        raise KernelRunError(f"the kernel exited {finished.returncode} on build-tables: {errors}")
    forwarded = _forward_stderr(errors, timings, arguments, timings_tag, verb="build-tables")
    try:
        lines = finished.stdout.decode().splitlines()
    except UnicodeDecodeError as error:
        raise KernelRunError(f"the kernel wrote non-UTF-8 build-tables output: {error}") from None
    digests: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        try:
            answer = json.loads(line)
        except json.JSONDecodeError as error:
            raise KernelRunError(f"build-tables line {number} is not JSON: {error.msg}") from None
        if not isinstance(answer, dict) or set(answer) != {"config", "digest"}:
            raise KernelRunError(f"build-tables line {number} is not a {{config, digest}} answer")
        digests[answer["config"]] = answer["digest"]
    if sorted(digests) != sorted(configs):
        raise KernelRunError(
            f"build-tables answered for {sorted(digests)} where {sorted(configs)} were asked for"
        )
    return TableBuild(digests, tuple(forwarded), peaks)


def memo_path(out_dir: Path, config: str) -> Path:
    """Where one configuration's packed memo sits beside its tables."""
    return Path(out_dir) / f"memo-{config}.tsv.gz"


@dataclass(frozen=True)
class MemoHead:
    """What a memo file's head line records: the configuration and mode set it was traced under, which the crate checks the file against, and the opaque stamp its writer chose, which `run_m1.previous_memos` passes to `run_m1.memo_edited`."""

    config: str
    modes: str
    stamp: str


def read_memo_head(path: Path) -> MemoHead | None:
    """The head of one packed memo, or None when there is no readable memo there — no file, another format, a gzip stream damaged before the head line ends, or a head short of its three fields."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            first = handle.readline()
    except OSError, EOFError, UnicodeDecodeError, zlib.error:
        return None
    marker, _tab, rest = first.rstrip("\n").partition("\t")
    if marker != f"# {MEMO_FORMAT}":
        return None
    fields = rest.split("\t", 2)
    if len(fields) != 3:
        return None
    return MemoHead(*fields)


class ReplayDisagreement(KernelRunError):
    """`replay-strings` found a window its rules and its engine settle differently, or one the engine refuses. The message is the crate's, naming the configuration, the window and the text it was reached in. A separate class lets a build tell this finding, a failed build naming a text, from a kernel interface failure."""


# The head token of the window memo `replay-strings` writes per configuration under `--memo-dir` (`rebuild/kernel-rs/src/replay.rs`'s `MEMO_FORMAT`); `conform.absorb_replay_memo` rejects a file with any other token.
REPLAY_MEMO_FORMAT = "ams-m1-replay-memo/1"


def replay_memo_dump(out_dir: Path, config: str) -> Path:
    """Where `replay-strings` writes one configuration's window memo under `out_dir` when asked (`fanout::replay_memo_path` builds the same name). It is a build input that `run_m1.run_replay_strings` absorbs into the configuration's settle memo and deletes in the same phase, not an artifact."""
    return Path(out_dir) / f"replay-windows-{config}.bin"


def replay_strings(
    spec: ResolvedSpec,
    out_dir: Path,
    configs: Sequence[str],
    *,
    max_length: int,
    families: Sequence[str] | None,
    threads: int,
    timings: bool = False,
    memo_dir: Path | None = None,
    memo_windows: int | None = None,
    last: str | None = None,
    on_peak: Callable[[int], None] | None = None,
) -> dict[str, dict[str, int]]:
    """Replay every named configuration's persisted rules under `out_dir` over every text up to `max_length`, and return `{config: {texts, windows, skipped}}` on a clean walk. With `families`, only the texts naming one of those runes are walked. With `last`, one symbol of the spec's alphabet (`labels.spec_alphabet`), only the texts ending in it are walked, as in the deep sweep's units (`run_m1.sweep_units`): the walks over every symbol partition the texts between them, and `skipped` counts only the texts ending in it that `families` leaves out. It reaches the subcommand as `--last=` and the symbol's code point, and a string that is not one character raises `ValueError` before anything is spawned, as does `last` beside `memo_dir`, since a memo written from one symbol's texts would hold only part of a configuration's windows. Rules apply first-match with the settled left fed forward, and each window is checked against the crate's own settlement. `windows` counts window settles: each distinct window once on a walk with no ceiling, and again each time it is met after a release. The spec is the same memoized dump the settlement functions and the guard sweep read.

    A disagreement or a refused window raises `ReplayDisagreement` with the crate's message. Every other failure the CLI contract distinguishes raises `KernelRunError`, as does a clean exit whose output names a different set of configurations from the one requested. An empty `families` raises `ValueError` before anything is spawned, because the subcommand treats it as a usage error and a caller with nothing to walk has nothing to ask.

    With `memo_dir`, each passing walk writes its window memo to `replay_memo_dump(memo_dir, config)`: every distinct window it settled, keyed in the crate's own form, with every distinct settled record beside them. The result is unchanged. The build asks for one on a full replay, so the settle memo every later phase loads is filled here instead of by the oracle. The deep walk (`rebuild/tools/deep_replay.py`) never asks for a memo and passes `memo_windows` instead, which reaches the subcommand as `--memo-windows=`: the most windows a walk keeps memoized before it releases its memos and continues. Passing both raises `ValueError` before anything is spawned, because a walk that released its memo holds only the windows settled since, and a walk that writes its memo has no ceiling. A ceiling below one window also raises.

    `on_peak`, when given, is called with the crate process's own peak RSS in bytes once a walk has answered cleanly (`_run_kernel_reaped`), before this returns. The walks are threads of that one process, so the figure covers every walk it ran at once. The build's replay (`run_m1.run_replay_strings`) and the deep replay (`rebuild/tools/deep_replay.py`) record it for `make job-costs`.
    """
    if families is not None and not families:
        raise ValueError("replay_strings takes a non-empty family list or None for every text")
    if last is not None and len(last) != 1:
        raise ValueError(f"replay_strings takes one symbol as the last, not {last!r}")
    if memo_windows is not None and memo_windows < 1:
        raise ValueError(f"replay_strings takes a memo ceiling of at least one window, not {memo_windows}")
    if memo_windows is not None and memo_dir is not None:
        raise ValueError(
            "replay_strings takes a memo directory or a memo ceiling, not both: a walk that writes its memo walks with no ceiling, since a released memo holds only the windows settled since the release"
        )
    if last is not None and memo_dir is not None:
        raise ValueError(
            "replay_strings takes a memo directory or a last symbol, not both: a walk over the texts ending in one symbol settles only part of its configuration's windows"
        )
    spec_path = _spec_dump(spec)
    ensure_built()
    arguments = [
        str(BINARY),
        "replay-strings",
        str(spec_path),
        str(out_dir),
        f"--configs={','.join(configs)}",
        f"--max-length={max_length}",
        f"--threads={threads}",
        *settlement_flags(),
    ]
    if families is not None:
        arguments.append(f"--families={','.join(families)}")
    if last is not None:
        arguments.append(f"--last=U+{ord(last):04X}")
    if memo_dir is not None:
        arguments.append(f"--memo-dir={memo_dir}")
    if memo_windows is not None:
        arguments.append(f"--memo-windows={memo_windows}")
    if timings:
        arguments.append("--timings")
    timeout = replay_timeout(
        symbols=len(labels.spec_alphabet(spec)),
        max_length=max_length,
        configs=len(configs),
        threads=threads,
        last=last is not None,
    )
    finished, peaks = _run_kernel_reaped(arguments, "replay-strings", timeout=timeout)
    errors = finished.stderr.decode(errors="replace").strip()
    if finished.returncode == 2:
        raise KernelRunError(
            f"kernel does not support replay-strings yet, or rejected the invocation as a usage error: {errors} ({' '.join(arguments)})"
        )
    if finished.returncode != 0:
        if "replay disagreement" in errors or "the engine refused the window" in errors:
            raise ReplayDisagreement(errors)
        raise KernelRunError(f"the kernel exited {finished.returncode} on replay-strings: {errors}")
    _forward_stderr(errors, timings, arguments, verb="replay-strings")
    try:
        lines = finished.stdout.decode().splitlines()
    except UnicodeDecodeError as error:
        raise KernelRunError(f"the kernel wrote non-UTF-8 replay-strings output: {error}") from None
    answered: dict[str, dict[str, int]] = {}
    for number, line in enumerate(lines, 1):
        try:
            answer = json.loads(line)
        except json.JSONDecodeError as error:
            raise KernelRunError(f"replay-strings line {number} is not JSON: {error.msg}") from None
        if not isinstance(answer, dict) or set(answer) != {"config", "texts", "windows", "skipped"}:
            raise KernelRunError(
                f"replay-strings line {number} is not a {{config, texts, windows, skipped}} answer"
            )
        answered[answer["config"]] = {key: int(answer[key]) for key in ("texts", "windows", "skipped")}
    if sorted(answered) != sorted(configs):
        raise KernelRunError(
            f"replay-strings answered for {sorted(answered)} where {sorted(configs)} were asked for"
        )
    if on_peak is not None and peaks.resident:
        on_peak(peaks.resident)
    return answered


class EmittedOrderDisagreement(KernelRunError):
    """`replay-emitted` found a row the shipped settlement order settles differently from its configuration's table. The message is the crate's, naming the configuration, the row, the continuation of its open slot, the emitted rule that fired and the table's own rule. A separate class lets a build tell this finding from a kernel interface failure."""


EMITTED_COUNTS = ("rows", "checked_per_member", "open_rows", "continued")
"""The counts the crate's `replay-emitted` answer line carries beside the configuration, in the order it writes them."""


def replay_emitted(
    windows: Path,
    *,
    config: str,
    table: Path,
    order: Path,
    context: Path,
    timings: bool = False,
) -> dict[str, int]:
    """Walk one configuration's packed window enumeration against the shipped settlement order with the crate's `replay-emitted` subcommand (`rebuild/kernel-rs/src/shipped_order.rs`). Returns `{rows, checked_per_member, open_rows, continued}` on a clean walk, where `checked_per_member` counts the rows tried member by member because an emitted lookahead class contained their deep class only in part, `open_rows` the rows whose key leaves a slot open after a letter (every continuation of which the walk checks), and `continued` those whose continuations it searched because their deciding rule constrains the open slot or a later one. `windows` is the `.gz` the build packed (`table.windows_path`). It is decompressed here and streamed to the subcommand's standard input, because the crate reads the plain payload and has no decompressor. `table` is the configuration's settlement TSV, `order` the file `emit_gsub.emitted_order_tsv` writes, and `context` the one `emit_gsub.emitted_context_tsv` writes. A disagreement raises `EmittedOrderDisagreement` with the crate's message; every other failure raises `KernelRunError`."""
    ensure_built()
    arguments = [
        str(BINARY),
        "replay-emitted",
        "-",
        f"--config={config}",
        f"--table={table}",
        f"--order={order}",
        f"--context={context}",
    ]
    if timings:
        arguments.append("--timings")
    read_end, write_end = os.pipe()
    try:
        with _relink_lock(fcntl.LOCK_SH):
            process = subprocess.Popen(
                arguments, stdin=read_end, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
    except FileNotFoundError:
        os.close(read_end)
        os.close(write_end)
        raise KernelRunError(
            f"no kernel binary at {BINARY} — run `make kernel-build` first, or let the caller's cargo_build() build it"
        ) from None
    os.close(read_end)

    unreadable: list[OSError] = []

    def pump() -> None:
        # The subcommand stops reading after its disagreement limit, so a pipe closed under the writer means the walk has already finished, not a kernel interface failure. The sink is opened first so that a payload that cannot be opened still closes the subcommand's standard input.
        try:
            with os.fdopen(write_end, "wb", buffering=1 << 20) as sink:
                try:
                    with gzip.open(windows, "rb") as source:
                        shutil.copyfileobj(source, sink, length=1 << 20)
                except BrokenPipeError:
                    raise
                except OSError as error:
                    unreadable.append(error)
        except BrokenPipeError:
            pass

    writer = threading.Thread(target=pump, name=f"replay-emitted[{config}]", daemon=True)
    writer.start()
    try:
        stdout, stderr = process.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise KernelRunError(
            f"the kernel gave no answer within {TIMEOUT} seconds on replay-emitted ({' '.join(arguments)})"
        ) from None
    finally:
        writer.join()
    if unreadable:
        raise KernelRunError(f"{windows}: {unreadable[0]}")
    errors = stderr.decode(errors="replace").strip()
    if process.returncode == 2:
        raise KernelRunError(
            f"kernel does not support replay-emitted yet, or rejected the invocation as a usage error: {errors} ({' '.join(arguments)})"
        )
    if process.returncode != 0:
        if "shipped-order disagreement" in errors:
            raise EmittedOrderDisagreement(errors)
        raise KernelRunError(f"the kernel exited {process.returncode} on replay-emitted: {errors}")
    _forward_stderr(errors, timings, arguments, verb="replay-emitted")
    try:
        answer = json.loads(stdout.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KernelRunError(f"the kernel's replay-emitted answer is not one JSON line: {error}") from None
    if (
        not isinstance(answer, dict)
        or set(answer) != {"config", *EMITTED_COUNTS}
        or answer["config"] != config
    ):
        raise KernelRunError(
            f"replay-emitted answered {answer!r} where a {{config: {config!r}, {', '.join(EMITTED_COUNTS)}}} line was asked for"
        )
    return {key: int(answer[key]) for key in EMITTED_COUNTS}


def _forward_stderr(
    errors: str,
    timings: bool,
    arguments: list[str],
    tag: str | None = None,
    verb: str = "enumerate-configs",
) -> list[str]:
    """Copy the kernel's timing lines to this process's stderr, return them as copied, and raise on anything else. Of the flags this module passes, `--timings` is the only one that writes to stderr on a clean exit, and it writes only `[t] <label> <secs>s` lines, buffered and written in `--configs` order. Copying them verbatim puts the kernel's per-configuration wall-clock times in the same journal as the Python stage's, since `cycle_timings` reads both from a step's captured output. A `tag` in brackets is appended to each label that does not already end in one, so a fan-out that runs one process per configuration stays attributable."""
    if not errors:
        return []
    lines = errors.split("\n")
    if not timings:
        raise KernelRunError(
            f"the kernel wrote to stderr on a clean {verb} exit: {errors} ({' '.join(arguments)})"
        )
    stray = [line for line in lines if not line.startswith("[t] ")]
    if stray:
        raise KernelRunError(
            f"the kernel wrote {len(stray)} non-timing lines to stderr on a clean {verb} exit: {stray[0]}"
        )
    copied = [_tagged(line, tag) if tag else line for line in lines]
    for line in copied:
        sys.stderr.write(line + "\n")
    sys.stderr.flush()
    return copied


def _tagged(line: str, tag: str) -> str:
    marker, _, rest = line.partition(" ")
    label, separator, tail = rest.partition(" ")
    if not separator or label.endswith("]"):
        return line
    return f"{marker} {label}[{tag}] {tail}"


def _json_result(text: str):
    """Parse one case result's text as JSON. This is the trace shape and the default decode for `_settle_cases`, so a caller without its own `decode` gets the parsed result."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise KernelRunError(f"the case result is not JSON: {error.msg}") from None


def _settle_cases(
    spec_path: Path,
    cases: Sequence[str],
    features: frozenset[str],
    modes: SettlementModes | None = None,
    decode=_json_result,
    settled_only: bool = False,
):
    """Run `settle-cases` over the already-dumped spec with the case lines on its stdin, and check that the kernel returned one case result per case line without changing or reordering any. The check is on bytes: the crate echoes each case line verbatim before its result, so an output line must start with its own case line and a tab, and the case line is never parsed back. A case line the crate cannot read makes it exit with an error instead of settling. `decode` reads one case result's text (everything after that tab) into whatever the caller keeps, the parsed JSON by default. It runs once per distinct case result text in the batch, since identical case results decode to the same value. That keeps a batch's Python cost proportional to the distinct case results: the review corpus's windows overlap heavily, so much of every batch repeats a trace already decoded. `settled_only` asks for the settled record's seven fields instead of the trace, which `settle_windows` reads through `_settled_of_fields`."""
    arguments = [str(BINARY), "settle-cases", str(spec_path), "-"]
    if features:
        arguments.append(f"--features={','.join(sorted(features))}")
    if settled_only:
        arguments.append("--settled-only")
    arguments.extend(settlement_flags(modes))
    finished = _run_kernel(
        arguments, "settle-cases", "".join(line + "\n" for line in cases).encode(), timeout=TIMEOUT
    )
    errors = finished.stderr.decode(errors="replace").strip()
    if finished.returncode == 2:
        raise KernelRunError(
            f"kernel does not support settle-cases yet, or rejected the invocation as a usage error: {errors} ({' '.join(arguments)})"
        )
    if finished.returncode != 0:
        raise KernelRunError(f"the kernel exited {finished.returncode} on settle-cases: {errors}")
    if errors:
        raise KernelRunError(f"the kernel wrote to stderr on a clean settle-cases exit: {errors}")
    try:
        lines = finished.stdout.decode().splitlines()
    except UnicodeDecodeError as error:
        raise KernelRunError(f"the kernel wrote non-UTF-8 settle-cases output: {error}") from None
    if len(lines) != len(cases):
        raise KernelRunError(f"settle-cases returned {len(lines)} case results for {len(cases)} case lines")
    results = []
    decoded: dict[str, object] = {}
    for line_number, (line, case) in enumerate(zip(lines, cases), 1):
        cut = len(case)
        if not line.startswith(case) or len(line) <= cut or line[cut] != "\t":
            raise KernelRunError(f"settle-cases line {line_number} changed or reordered its case line")
        text = line[cut + 1 :]
        try:
            value = decoded[text]
        except KeyError:
            try:
                value = decoded[text] = decode(text)
            except KernelRunError as error:
                raise KernelRunError(f"settle-cases line {line_number}: {error}") from None
        results.append(value)
    return results


def _settle_batch(
    spec: ResolvedSpec,
    cases: Sequence[str],
    features: frozenset[str],
    modes: SettlementModes | None,
    decode,
    settled_only: bool = False,
) -> list:
    """Settle one batch in one invocation. The spec dump is the memoized one, and the case lines go over a pipe, so a batch writes no file."""
    if not cases:
        return []
    spec_path = _spec_dump(spec)
    ensure_built()
    return _settle_cases(spec_path, cases, frozenset(features), modes, decode, settled_only)


def settle_cases(
    spec: ResolvedSpec,
    cases: Sequence[str],
    features: frozenset[str],
    modes: SettlementModes | None = None,
    decode=None,
) -> list:
    """Settle a batch of windows through the crate in one invocation. The cases are case lines (`case_line` builds one), and each case result is the crate's whole trace. Without `decode`, the list holds each window's parsed result, which `trace_of` reads into a `TransitionTrace`. With `decode`, it holds what `decode` returned for each parsed result (`settle_sequences` wants a trace or a refusal), decoded once per distinct result in the batch (`_settle_cases`), so a caller that wants one model value per window never builds a list of result dictionaries. A caller that wants only the settled record should use `settle_windows`, which asks the crate for that record instead of a trace. `modes` names the settlement mode set; without it the process's defaults apply."""
    if decode is None:
        return _settle_batch(spec, cases, features, modes, _json_result)
    return _settle_batch(spec, cases, features, modes, lambda text: decode(_json_result(text)))


# The index of the rune being settled among a case line's tab-separated fields: the left's kind and its seven record fields come first, the four right slots after.
CASE_INPUT_FIELD = 8
_NO_RECORD = ("",) * 7


def case_line(left: settle.LeftContext, token: settle.RightToken, rights: Sequence[settle.RightToken]) -> str:
    """One independent window as a `settle-cases` case line, tab-separated: the left's kind and its record (rune, stance, entry, exit, comma-joined adjustments, junction, extension; all seven empty for a left with no record, and a height or junction empty where there is none), then the rune being settled and the four raw slots after it, each a rune name or the kind name of a boundary or unknown slot. The crate echoes the line verbatim before its result, which is how a batch's case results are matched to its case lines."""
    settled = left.settled
    if settled is None:
        record = _NO_RECORD
    else:
        cell = settled.cell
        record = (
            cell.rune,
            cell.stance,
            cell.entry or "",
            cell.exit or "",
            ",".join(cell.adjustments),
            settled.junction or "",
            str(settled.extension),
        )
    return "\t".join(
        (
            left.kind,
            *record,
            token.letter,
            *(right.letter if right.kind == "letter" else right.kind for right in rights),
        )
    )


def _refusal(result: Mapping) -> None:
    """Raise the crate's refusal result as `settle.SettleError`. A well-formed refusal is `{raise, message}` and nothing else: the raise identity becomes the error's bucket and the crate's message becomes the error message verbatim, so nothing downstream has to parse text to tell refusals apart. Anything else with a `raise` key means the kernel interface is wrong, not the window, and raises `KernelRunError`."""
    if "raise" not in result:
        return
    bucket = result.get("raise")
    message = result.get("message")
    if set(result) != {"raise", "message"} or not isinstance(bucket, str) or not isinstance(message, str):
        raise KernelRunError(f"settle-cases returned a malformed refusal: {result!r}")
    raise settle.SettleError(message, bucket)


# The pieces of a trace repeat far more than traces do: a batch of tens of thousands of case results names a few hundred distinct candidates, ranked candidates, eliminations and settled cells, and building a frozen dataclass costs more than looking one up. So each piece is built once per distinct row and shared, keyed on the row's values; sharing is invisible to a reader because every piece is immutable. A table that reaches `_INTERN_CAP` entries is cleared, which bounds each kind at that many objects in a long process.
_INTERN_CAP = 8192
_CANDIDATES: dict[tuple, settle.Candidate] = {}
_RANKED_CANDIDATES: dict[tuple, settle.RankedCandidate] = {}
_ELIMINATIONS: dict[tuple, settle.Elimination] = {}
_SETTLED: dict[tuple, Settled] = {}


def _interned(table: dict, key: tuple, build):
    value = table.get(key)
    if value is None:
        if len(table) >= _INTERN_CAP:
            table.clear()
        value = table[key] = build()
    return value


def _candidate_of(row) -> settle.Candidate:
    if not isinstance(row, list) or len(row) != 5:
        raise KernelRunError(f"settle-cases returned a malformed candidate: {row!r}")
    try:
        return _interned(_CANDIDATES, tuple(row), lambda: settle.Candidate(*row))
    except TypeError:
        raise KernelRunError(f"settle-cases returned a malformed candidate: {row!r}") from None


def _ranked_candidate_of(row) -> settle.RankedCandidate:
    if not isinstance(row, list) or len(row) != 3 or not isinstance(row[0], list):
        raise KernelRunError("settle-cases returned a malformed ranking")
    try:
        return _interned(
            _RANKED_CANDIDATES,
            (tuple(row[0]), row[1], row[2]),
            lambda: settle.RankedCandidate(_candidate_of(row[0]), row[1], row[2]),
        )
    except TypeError:
        raise KernelRunError("settle-cases returned a malformed ranking") from None


def _elimination_of(row) -> settle.Elimination:
    if not isinstance(row, list) or len(row) != 3:
        raise KernelRunError("settle-cases returned malformed eliminations")
    try:
        return _interned(
            _ELIMINATIONS, tuple(row), lambda: settle.Elimination(row[0], row[1], _provenance_of(row[2]))
        )
    except TypeError:
        raise KernelRunError("settle-cases returned malformed eliminations") from None


def settled_of_row(row) -> Settled:
    """Decode one settled record in the crate's JSON form (`types::settled_json`), `{"cell": [rune, stance, entry, exit, [adjustments]], "junction": …, "extension": …}`, to an interned `Settled`. A `settle-cases` trace carries this shape under `settled`, and the replay's window memo carries one per line, so both decode here. The interning key is `(rune, stance, entry, exit, adjustments, junction, extension)` with each absent height `None`. `_settled_of_fields` builds the same key from the tab-separated form, so a record read from either result shape is the same object."""
    if not isinstance(row, Mapping) or set(row) != {"cell", "junction", "extension"}:
        raise KernelRunError(f"the kernel spelled a malformed settled record: {row!r}")
    cell_row = row["cell"]
    if not isinstance(cell_row, list) or len(cell_row) != 5 or not isinstance(cell_row[4], list):
        raise KernelRunError(f"the kernel spelled a malformed cell: {cell_row!r}")
    try:
        return _interned(
            _SETTLED,
            (*cell_row[:4], tuple(cell_row[4]), row["junction"], row["extension"]),
            lambda: Settled(
                CellId(cell_row[0], cell_row[1], cell_row[2], cell_row[3], tuple(cell_row[4])),
                row["junction"],
                row["extension"],
            ),
        )
    except TypeError:
        raise KernelRunError(f"the kernel spelled a malformed cell: {cell_row!r}") from None


def _settled_of(result) -> Settled:
    """The settled cell alone from a trace, for a caller that does not need the ranking that chose it."""
    if not isinstance(result, Mapping):
        raise KernelRunError(f"settle-cases returned a malformed result: {result!r}")
    _refusal(result)
    return settled_of_row(result.get("settled"))


# The number of fields in a settled-only case result, in `types::settled_fields` order: the same seven a case line uses for its left record.
_SETTLED_FIELDS = 7


def _settled_of_fields(text: str) -> Settled:
    """Decode one settled-only case result to an interned `Settled`. The result is seven tab-separated fields (rune, stance, entry, exit, comma-joined adjustments, junction, extension, with a height empty where there is none), or, starting with `{`, the crate's refusal object, which `_refusal` raises as it does for a trace. The interning key is the one `settled_of_row` builds, so a record read from either result shape is the same object."""
    if text.startswith("{"):
        result = _json_result(text)
        if not isinstance(result, Mapping):
            raise KernelRunError(f"settle-cases returned a malformed result: {result!r}")
        _refusal(result)
        raise KernelRunError(f"settle-cases returned an object for a settled-only case line: {text!r}")
    fields = text.split("\t")
    if len(fields) != _SETTLED_FIELDS:
        raise KernelRunError(f"the kernel spelled a malformed settled record: {text!r}")
    rune, stance, entry, exit_height, adjustments, junction, extension = fields
    try:
        extension_value = int(extension)
    except ValueError:
        raise KernelRunError(f"the kernel spelled a malformed settled record: {text!r}") from None
    entry_height = entry or None
    exit_height = exit_height or None
    adjustment_tokens = tuple(adjustments.split(",")) if adjustments else ()
    junction_height = junction or None
    return _interned(
        _SETTLED,
        (rune, stance, entry_height, exit_height, adjustment_tokens, junction_height, extension_value),
        lambda: Settled(
            CellId(rune, stance, entry_height, exit_height, adjustment_tokens),
            junction_height,
            extension_value,
        ),
    )


def _provenance_of(pointer) -> Provenance | None:
    if pointer is None:
        return None
    if not isinstance(pointer, str) or ":" not in pointer:
        raise KernelRunError(f"settle-cases returned a malformed provenance pointer: {pointer!r}")
    file, path = pointer.rsplit(":", 1)
    return Provenance(file, path)


def trace_of(result) -> settle.TransitionTrace:
    """Read one case result's parsed `result` into the trace every author-facing consumer renders: the settled cell, the ranked candidates, the eliminations with their YAML provenance, the deciding stage, the runner-up and the notes."""
    if not isinstance(result, Mapping):
        raise KernelRunError(f"settle-cases returned a malformed result: {result!r}")
    _refusal(result)
    expected = {
        "settled",
        "prospect",
        "joint_tiebreak",
        "notes",
        "fired",
        "decided_stage",
        "runner_up",
        "ranked",
        "eliminations",
    }
    if set(result) != expected:
        raise KernelRunError(
            f"settle-cases returned trace fields {sorted(result)}, expected {sorted(expected)}"
        )
    for field_name in ("notes", "fired", "ranked", "eliminations"):
        if not isinstance(result[field_name], list):
            raise KernelRunError(
                f"settle-cases returned a malformed {field_name} field: {result[field_name]!r}"
            )
    ranked = tuple(map(_ranked_candidate_of, result["ranked"]))
    eliminations = tuple(map(_elimination_of, result["eliminations"]))
    runner_up = None if result["runner_up"] is None else _candidate_of(result["runner_up"])
    return settle.TransitionTrace(
        settled=_settled_of(result),
        joint_tiebreak=result["joint_tiebreak"],
        prospect=result["prospect"],
        ranked=ranked,
        eliminations=eliminations,
        decided_stage=result["decided_stage"],
        runner_up=runner_up,
        notes=tuple(result["notes"]),
    )


def _tolerated_settled_fields(text: str, buckets: frozenset[str] | None = None) -> Settled | None:
    """`_settled_of_fields`, returning `None` for a refusal instead of raising: any refusal when `buckets` is `None`, otherwise only a refusal whose bucket `buckets` names, with any other refusal still raised. Only the crate's own refusal is caught: a malformed case result still raises `KernelRunError`, because it means the kernel interface is wrong, not the window."""
    try:
        return _settled_of_fields(text)
    except settle.SettleError as error:
        if buckets is not None and error.bucket not in buckets:
            raise
        return None


def _trace_or_refusal(result) -> settle.TransitionTrace | settle.SettleError:
    """`trace_of`, returning a refusal instead of raising it. A batch decodes each distinct result once, so the refusal has to be carried back to the sequence that asked, which is the only place that knows whether to raise it or drop the sequence. A malformed case result still raises `KernelRunError`."""
    try:
        return trace_of(result)
    except settle.SettleError as error:
        return error


def settle_windows(
    spec: ResolvedSpec,
    cases: Sequence[str],
    features: frozenset[str],
    batch: int = SETTLE_WINDOW_BATCH,
    modes: SettlementModes | None = None,
    on_error: str = "raise",
    tolerate: frozenset[str] = frozenset(),
) -> list[Settled | None]:
    """One `Settled` per case, in the order the cases were given, decoded directly from each output line. The conform walker and the ligature outgoing check use this: they keep only each window's outcome, so it asks the crate for the settled record alone (`--settled-only`, seven tab-separated fields) instead of a trace whose ranking nothing reads. `batch` limits how many windows one invocation takes.

    `on_error="raise"` raises a refusal from the batch that met it, with the crate's message naming the left and the input, except that a refusal whose bucket `tolerate` names gives `None`. The ligature outgoing check tolerates `E-UNREACHABLE` this way, because a window no candidate can settle is a missing capability to it, while any other refusal is a fault it must report. `on_error="drop"` puts `None` in the slot of every refused case and decodes every other line as usual. A caller that settles windows it did not choose (the witness stage, which prefills every candidate string it might read) wants the other results, and wants a refusal to surface only where something reads that window. A malformed case result means the kernel interface is wrong, not the window, and raises `KernelRunError` in either mode.
    """
    if on_error == "drop":
        decode = _tolerated_settled_fields
    elif tolerate:
        decode = lambda text: _tolerated_settled_fields(text, tolerate)
    else:
        decode = _settled_of_fields
    out: list[Settled | None] = []
    for start in range(0, len(cases), batch):
        out.extend(
            _settle_batch(spec, cases[start : start + batch], features, modes, decode, settled_only=True)
        )
    return out


@dataclass
class _SequenceState:
    tokens: tuple[settle.RightToken, ...]
    features: frozenset[str]
    traces: list[settle.TransitionTrace] = field(default_factory=list)
    left: settle.LeftContext = settle.LeftContext("edge")
    dropped: bool = False


def settle_sequences(
    spec: ResolvedSpec,
    requests: Sequence[tuple[Sequence[settle.RightToken], frozenset[str]]],
    *,
    on_error: str = "raise",
    modes: SettlementModes | None = None,
) -> list[list[settle.TransitionTrace] | None]:
    """A trace per position for each already-formed token sequence, in the order the sequences were given. `settle-cases` settles independent windows, but a sequence's next left context is the previous window's result, so the batch advances in waves: every sequence's first position, then every sequence's second position using the first wave's results. Each wave costs one invocation per feature configuration per `SETTLE_CASE_BATCH_SIZE` windows instead of one per sequence. Boundary positions are not sent to the kernel: they settle to a model constant and reset the left here.

    The caller does tokenization and formation, since a caller that already has its tokens should not have to pass codepoints to re-derive them; `settle_codepoints` does both. `on_error="raise"` raises a refusal at the wave that met it, with the crate's message naming the left and the input. `on_error="drop"` returns `None` for that one sequence and lets every other sequence in the wave finish. A caller sweeping texts it does not control (the table diff's `ExampleIndex`) wants the other results, not the first error.
    """
    states = [
        _SequenceState(tokens=tuple(tokens), features=frozenset(features)) for tokens, features in requests
    ]
    max_positions = max((len(state.tokens) for state in states), default=0)
    for position in range(max_positions):
        batches: dict[frozenset[str], list[tuple[_SequenceState, str]]] = {}
        for state in states:
            if state.dropped or position >= len(state.tokens):
                continue
            token = state.tokens[position]
            if token.kind != "letter":
                state.traces.append(
                    settle.TransitionTrace(
                        settle.boundary_settled(token.kind), False, 0, (), (), "boundary", None, ()
                    )
                )
                state.left = settle.LeftContext(token.kind)
                continue
            rights = tuple(
                state.tokens[index] if index < len(state.tokens) else settle.EDGE
                for index in range(position + 1, position + 5)
            )
            batches.setdefault(state.features, []).append((state, case_line(state.left, token, rights)))
        for features, pending in batches.items():
            for start in range(0, len(pending), SETTLE_CASE_BATCH_SIZE):
                chunk = pending[start : start + SETTLE_CASE_BATCH_SIZE]
                results = settle_cases(
                    spec, [case for _state, case in chunk], features, modes=modes, decode=_trace_or_refusal
                )
                for (state, _case), trace in zip(chunk, results):
                    if isinstance(trace, settle.SettleError):
                        if on_error != "drop":
                            raise trace
                        state.dropped = True
                        continue
                    state.traces.append(trace)
                    state.left = settle.LeftContext("letter", trace.settled)
    return [None if state.dropped else state.traces for state in states]


def settle_codepoints(
    spec: ResolvedSpec,
    codepoints: Sequence[int],
    features: frozenset[str],
    guard_verdicts: FormationGuard | None = None,
) -> list[Settled]:
    """Settle one text end to end: tokenize it, form its ligatures against the guard verdicts, settle every position through the crate, and return the settled cells. A caller that already has a sweep passes it as `guard_verdicts` to skip the memo lookup."""
    if guard_verdicts is None:
        guard_verdicts = guard_sweep(spec)
    tokens = settle.form_ligatures(spec, settle.tokens_from_codepoints(spec, codepoints), guard_verdicts)
    traces = settle_sequences(spec, [(tokens, frozenset(features))])[0]
    assert traces is not None
    return [trace.settled for trace in traces]


def _guard_verdicts(spec: ResolvedSpec, spec_path: Path, config: str | None = None) -> FormationGuard:
    """Run `guard-sweep` over one already-dumped spec and parse its complete output. Without `config` the verdicts quantify over the powerset; with one they are that configuration's, named as `conform.ACCEPTANCE_CONFIGS` names it so the no-feature configuration can be named. Completeness and uniqueness are checked here instead of left to a consumer's lookup miss, because a clean kernel exit that omitted or duplicated a row is a kernel interface failure, not an emitter error."""
    arguments = [str(BINARY), "guard-sweep", str(spec_path)]
    if config is not None:
        arguments.append(f"--config={config}")
    finished = _run_kernel(arguments, "guard-sweep", timeout=TIMEOUT)
    errors = finished.stderr.decode(errors="replace").strip()
    if finished.returncode == 2:
        raise KernelRunError(
            f"kernel does not support guard-sweep yet, or rejected the invocation as a usage error: {errors} ({' '.join(arguments)})"
        )
    if finished.returncode != 0:
        raise KernelRunError(f"the kernel exited {finished.returncode} on guard-sweep: {errors}")
    if errors:
        raise KernelRunError(f"the kernel wrote to stderr on a clean guard-sweep exit: {errors}")
    try:
        lines = finished.stdout.decode().splitlines()
    except UnicodeDecodeError as error:
        raise KernelRunError(f"the kernel wrote non-UTF-8 guard-sweep output: {error}") from None

    rune_names = frozenset(spec.runes)
    ligature_names = frozenset(name for name, rune in spec.runes.items() if rune.sequence)
    verdicts: FormationGuard = {}
    for line_number, line in enumerate(lines, 1):
        fields = line.split("\t")
        if len(fields) != 4:
            raise KernelRunError(
                f"guard-sweep line {line_number} has {len(fields)} tab-separated fields, expected 4: {line!r}"
            )
        ligature, right1_name, right2_name, verdict = fields
        if ligature not in ligature_names:
            raise KernelRunError(
                f"guard-sweep line {line_number} names non-ligature {ligature!r} as its ligature"
            )
        if right1_name not in rune_names:
            raise KernelRunError(
                f"guard-sweep line {line_number} names unknown first-slot rune {right1_name!r}"
            )
        right1 = settle.RightToken("letter", right1_name)
        if right2_name in rune_names:
            right2 = settle.RightToken("letter", right2_name)
        else:
            right2 = GUARD_TAIL_TOKENS.get(right2_name)
            if right2 is None:
                raise KernelRunError(
                    f"guard-sweep line {line_number} names unknown second-slot token {right2_name!r}"
                )
        if verdict not in ("blocked", "free"):
            raise KernelRunError(
                f"guard-sweep line {line_number} has unknown verdict {verdict!r}, expected 'blocked' or 'free'"
            )
        key = (ligature, right1, right2)
        if key in verdicts:
            raise KernelRunError(f"guard-sweep line {line_number} duplicates {fields[:3]}")
        verdicts[key] = verdict == "blocked"

    letters = tuple(settle.RightToken("letter", name) for name in sorted(rune_names))
    second_slots = (*letters, *GUARD_TAIL_TOKENS.values())
    expected = {
        (ligature, right1, right2)
        for ligature in ligature_names
        for right1 in letters
        for right2 in second_slots
    }
    missing = expected - verdicts.keys()
    extra = verdicts.keys() - expected
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"missing {len(missing)}")
        if extra:
            detail.append(f"carrying {len(extra)} unexpected")
        raise KernelRunError(
            f"guard-sweep returned an incomplete guard verdict map ({', '.join(detail)} verdicts)"
        )
    return verdicts


def guard_sweep(spec: ResolvedSpec) -> FormationGuard:
    """The crate's complete configuration-independent section 5.7 guard verdicts for `spec`, parsed into Python model tokens. One `guard-sweep` invocation runs per spec identity per process, however many callers ask. The first sweep in a process takes about a fifth of a second and a repeat about a twentieth, and the mapping has one entry per ligature, first-slot rune and second-slot token. Formation comes before every other stage, so a corpus build, an emitter and a walker in one process would otherwise each spawn for the same result. The returned mapping is the memo's own and is shared; treat it as read-only and copy it before changing it."""
    key = id(spec)
    with _GUARD_SWEEPS_LOCK:
        held = _GUARD_SWEEPS.get(key)
        if held is not None and held[0] is spec:
            _GUARD_SWEEPS.move_to_end(key)
            return held[1]
        spec_path = _spec_dump(spec)
        ensure_built()
        verdicts = _guard_verdicts(spec, spec_path)
        _GUARD_SWEEPS[key] = (spec, verdicts)
        _GUARD_SWEEPS.move_to_end(key)
        while len(_GUARD_SWEEPS) > _GUARD_SWEEPS_CAP:
            _GUARD_SWEEPS.popitem(last=False)
        return verdicts


def guard_sweep_under(spec: ResolvedSpec, features: frozenset[str]) -> FormationGuard:
    """The section 5.7 guard verdicts as one configuration alone produces them, with the same keys as `guard_sweep`'s. Every call is a crate invocation, with no memo, because nothing that ships reads per-configuration verdicts. The rebuild tests call it once per configuration to compare each configuration's verdicts with the quantified ones."""
    spec_path = _spec_dump(spec)
    ensure_built()
    return _guard_verdicts(spec, spec_path, "+".join(sorted(features)) or "default")


def read_stream(stream: Path) -> FixpointProduct:
    """Read one kernel stream back as a `FixpointProduct`. `enumerate-configs` writes plain ndjson, which `kernel_io.read_transitions` reads from the open handle; the gzip it applies to a path is for the artifacts under `rebuild/out/`. The file is deleted once the product is read, because a live configuration's stream is hundreds of megabytes and a whole cycle's would otherwise stay in the scratch directory for the rest of the build."""
    with stream.open("rt", encoding="utf-8") as handle:
        product = kernel_io.read_transitions(handle)
    stream.unlink()
    return product


def enumerate_transitions(spec: ResolvedSpec, features: frozenset[str]) -> FixpointProduct:
    """One configuration's reachable windows, enumerated by the crate and parsed into a `table.FixpointProduct`. The product holds everything the fold reads and nothing else the engine touched, so a consumer that wants what a table drops (the settled cells, the junctions, the optimistic prospect, the fired provenance per row) asks for the product. Only tests call this; `build_tables` folds in the crate without writing a stream. Nothing survives the call: the spec dump and the stream live in a scratch directory removed on return."""
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        spec_path = directory / "spec.json"
        kernel_io.write_spec(spec, spec_path)
        ensure_built()
        streams = enumerate_configs(
            spec_path, directory / "streams", [feature_config_token(features)], threads=1
        )
        return read_stream(next(iter(streams.values())))


def build_tables(spec: ResolvedSpec, features: frozenset[str]) -> tuple[DecisionTable, JoinTable]:
    """One configuration's decision and join tables, in memory, leaving no files: one `build-tables` process over a scratch spec dump, then the windows payload and the join TSV read back. The rows are read in full here, unlike on the build's own path, because a caller of this function wants the table, and the table is fixture-sized; `run_m1.build_tables` builds the live alphabet's. The crate raises its own errors during enumeration and folding (E-UNACCEPTED-EXIT, and the fold's checks listed in `rebuild/kernel-rs/src/fold.rs`), so a returned table has passed them."""
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        spec_path = directory / "spec.json"
        kernel_io.write_spec(spec, spec_path)
        ensure_built()
        config = feature_config_token(features)
        tables = directory / "tables"
        build_table_files(
            spec_path,
            tables,
            [config],
            inputs=UNSTAMPED_WINDOWS,
            threads=1,
            symbols=len(labels.spec_alphabet(spec)),
        )
        with (tables / f"windows-{config}.tsv").open("rt", encoding="utf-8") as handle:
            _stamp, decision = table.read_windows(handle)
        return decision, table.read_join_tsv(tables / f"joins-{config}.tsv")
