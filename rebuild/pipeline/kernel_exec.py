"""The kernel interface: the Python side of the Rust kernel. It builds the binary, runs the table build and the stream fan-out, reads the section 5.7 guard verdicts, settles batched windows for explain, review, conform and the tests, runs the string and shipped-order replays, and returns single-configuration products and tables to callers other than `run_m1`. It lives in the pipeline because the pipeline calls it. The `rebuild/tools/kernel_*.py` scripts are separate measurement harnesses.

The semantics defaults live here beside the flags that pass them to the kernel. `SIMULATED_PROSPECT_DEFAULT` and `FOLLOWER_PREFER_SLOTS_DEFAULT` control settlement, and `DEEP_CLASSES_DEFAULT` controls enumeration. Each is a module attribute read at call time, so a process sets them through the environment and a test can monkeypatch them. A caller that wants a different mode set builds a `SettlementModes` and passes it to `settle_cases`, `settle_windows` or `settle_sequences`.

The build is `cargo build --release` against the crate's manifest. Release is the only profile anything in the repository runs: the pipeline and the spec-echo test in `rebuild/test_kernel_io.py` both run `target/release/ams-m1-kernel`, and a debug binary is too slow to substitute for it. A machine without `cargo` gets a `KernelBuildError` that says how to install it. `ensure_built` builds once per process, and every caller in the process shares that build. `make kernel-build` runs the same build by hand through `main`.

`build_table_files` is what `run_m1` calls. One `build-tables` process enumerates every settlement configuration and folds each in place. It writes each configuration's settlement TSV, join TSV and plain window enumeration, and returns the table digest of each configuration's pair of tables with the crate's `[t]` lines and the process's own resident peak and peak footprint (`TableBuild`). No transition stream is written, because the fold reads the product the worklist still holds; that saves writing, reading and parsing several hundred megabytes per configuration. The configurations after `default` are deltas over it. `default` enumerates first and keeps its trace memo, and each other configuration reads that memo for every window whose key names none of its own unlocking runes and whose settlement read none of them, and traces only the rest; `rebuild/kernel-rs/src/memo.rs` gives the argument. Where memory has room, the heaviest of them enumerate from scratch beside `default` instead, from the start (`deltas_from_scratch`); when every delta does, `default` keeps no memo. The memo lives only inside the process: every build enumerates cold, and none reads or writes a memo file. Before any table is written, the configurations exchange windows (`rebuild/kernel-rs/src/crossconfig.rs`): each configuration's table takes in the windows another configuration keeps live where its own rules would answer them wrongly ahead of every right answer in the order the font ships, so a configuration's tables depend on the whole set it was built with. The windows head lists what each table took in (`table.DecisionTable.imports`), and a guard rule that only such a window reaches carries a certificate the witness stage settles under the configuration the window is live in (`witness.GUARD_MARKER`).

`enumerate_configs` is the stream fan-out. One process enumerates every named configuration and writes each one's transition stream to its own file. Nothing on the build's path calls it; `enumerate_transitions` and the tests do.

The table build's width is limited by memory, because a live configuration holds its whole working set until it has emitted. Each configuration runs on its own thread from its enumeration to its files, and its prepared product waits parked and compact from the end of its preparation until every configuration has parked, so at the end of the delta wave every configuration's parked product is resident beside the last delta's enumeration; then every configuration reads its product back whole for the cross-configuration exchange (`doc/parallelism.md`). `table_build_booking_bytes` is the memory a build at a given width is booked at: `DEFAULT_MEMO_BYTES` for the `default` memo snapshot kept alive for the wave, `PARKED_FOLD_BYTES` for each configuration's parked product, `DELTA_SLOT_BYTES` for each delta in flight beyond its parked product, and `FOLD_PREPARATION_BYTES` for a slot that runs only `default`'s fold preparation, which a width above the delta count has, and every width is booked at no less than the rendezvous, `READ_BACK_FOLD_BYTES` for each configuration's product read back. A build in which every configuration enumerates from scratch books each slot at `SCRATCH_PEAK_BYTES` less its parked product instead, and so does each delta a build starts from scratch beside `default`, in place of a delta's slot; such a build is booked at no less than its start, one `SCRATCH_PEAK_BYTES` for each of them and, for `default`, `UNCAPPED_SCRATCH_PEAK_BYTES` while a delta is left to read its memo, or one more `SCRATCH_PEAK_BYTES` once none is. `kernel_threads_default` is the widest width, up to the configuration count, whose booking fits the machine's memory less the reserve that policy states, floored at one; `deltas_from_scratch` then decides how many deltas start from scratch beside `default` at that width, and never narrows it. A larger machine, or a container limited by its cgroup, gets its own width without editing a constant. `AMS_KERNEL_THREADS` overrides the arithmetic in either direction. The artifacts are byte-identical at any width, so the override changes only memory use. Callers cap the width, a stated one included, at the number of settlement configurations (`conform.SETTLEMENT_CONFIGS`; the overlay configuration is never enumerated) and at the usable cores. The string replay after a build has its own divisor, `REPLAY_PEAK_BYTES`. `replay_threads_default` divides by it with nothing subtracted first, because the replay's engines are built after the build process has exited and no memo outlives it. `AMS_REPLAY_THREADS` overrides it.

`build_tables` and `enumerate_transitions` are the single-configuration forms, and neither writes anything that outlives the call. A single configuration has no other configuration to take windows from, so `build_tables` gives the same windows and joins as a build of the whole set but can give a different settlement table. Each dumps one spec to a scratch directory. `build_tables` runs `build-tables` into that directory and reads the two tables back through `table.read_windows` and `table.read_join_tsv`. `enumerate_transitions` enumerates the raw product as a stream and parses it into a `table.FixpointProduct`. Only tests call either.

`guard_sweep` runs `guard-sweep` once and returns the complete mapping from `(ligature, first raw slot, second raw slot)` to the configuration-independent formation verdict. It is memoized per spec identity, so a process sweeps a spec once however many callers ask. `guard_sweep_under` returns the same mapping for one named configuration instead of quantifying over the powerset. It is not memoized, because only tests read it; they compare each configuration's verdicts with the quantified ones.

The settlement and replay functions share the memoized spec dump. `replay_strings` is the enumeration-completeness check `run_m1.run_replay_strings` runs after every table build. One `replay-strings` process reads the settlement TSVs the build wrote and walks every text up to the maximum length, or only the texts naming the families the caller says changed, checking each window against the crate's own settlement. A disagreement raises `ReplayDisagreement` with the crate's message, which names the configuration, the window and the text. `settle_cases` is the raw form. It writes one tab-separated case line per independent window (`case_line` writes one) and returns the full Rust trace for each. It checks the case result count and that each output line starts with its case line verbatim, and decodes each distinct result once however many windows returned it. `settle_windows` asks the crate for the settled record alone (seven tab-separated fields under `--settled-only`) and decodes each straight to a `Settled`. The conform walker and the ligature outgoing check use it because they need only outcomes, not traces. Like `settle_sequences`, it takes an `on_error` argument, so a caller prefilling windows it may never read can get `None` for a refused window and keep the rest of the batch, and its `tolerate` argument gives `None` for only the refusal buckets it names. `settle_sequences` is what explain, the probe and the review corpus call. The subcommand takes independent windows, but a sequence's next left context is the previous window's result, so a batch of sequences advances in waves: every sequence's first position, then every second position using the first wave's results. Boundary positions are settled locally because their results are model constants. `settle_codepoints` settles one text. The CLI writes boundary tokens as `edge`, `space`, `zwnj`, `namer-dot` and `unknown`. The guard parser converts them to the `RightToken` constants in `settle`, so consumers never confuse these model tokens with glyph names such as `uni200C` or `periodcentered`.

The codecs between the transport lines and the pipeline's model types live here too, because every settlement caller needs them: `case_line` for case lines, and `trace_of` and `_settled_of_fields` for case results. A window the crate refuses returns `{raise, message}`. That becomes a `settle.SettleError` carrying the crate's error code as its bucket and its message verbatim, so a caller can sort refusals without parsing text. Any other malformed case result means the kernel interface itself is wrong and raises `KernelRunError`.

Every invocation is waited for up to a limit, and one that has not answered by then is killed and raises `KernelRunError` (`_run_kernel`'s `timeout`). The verbs that answer in seconds or minutes at any alphabet size wait `TIMEOUT`. `build-tables` and `replay-strings` grow with the alphabet, so each derives its limit at the call from the size of the spec's alphabet (`build_tables_timeout`, `replay_timeout`), and neither waits less than `TIMEOUT`.

Every invocation's result is checked strictly against the CLI contract. Exit 2 is a usage error, which for a well-formed invocation means the subcommand is missing or the flag sets on the two sides have diverged. Any other nonzero exit is the kernel rejecting its inputs. Output on stderr after a clean exit is a failure unless timings were requested. In that case every `[t]` line is copied to this process's stderr verbatim, so the cycle journal records the kernel's per-configuration wall-clock times the same way it records Python's, and any other stderr line is still a failure. Enumeration writes its results to files, so any stdout output there is a failure. `build-tables` also writes files but reports its digests on stdout, one JSON object per line, and the configurations they name are checked against the ones requested. `guard-sweep` writes every verdict to stdout as TSV, which is parsed strictly.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import functools
import gzip
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, wait
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
# What `build_table_files` and `replay_strings` add to the environment of the `build-tables` and `replay-strings` processes they spawn on macOS (`_allocator_environment`), the pipeline's two kernel verbs whose memos grow through doublings into tables of gigabytes. The string replay and each deep replay unit (`rebuild/tools/deep_replay.py`) run through `replay_strings`. `MallocLargeCache=0` turns off the allocator's cache of freed large allocations, so a table that a memo, the fixpoint's base index or its item table outgrows goes back to the system when it is freed, rather than staying resident and in the footprint until the kernel reclaims it at a time of its own choosing. The tables, a walk's counts and its window memo are the same bytes either way. Every table-build term below, DELTA_SLOT_BYTES through TABLE_BUILD_PEAK_BYTES, is measured with it set (SCRATCH_PEAK_BYTES's comment has the readings without it), and so are REPLAY_PEAK_BYTES and `deep_replay.DEEP_REPLAY_PEAK_BYTES`, whose comments have the readings without it.
KERNEL_ALLOCATOR_ENVIRONMENT = {"MallocLargeCache": "0"}
# What one delta in flight adds to the table build's peak beyond its own parked product (PARKED_FOLD_BYTES), when it runs over default's memo: the per-slot term of `table_build_booking_bytes`, and the divisor of `kernel_threads_default` below the configuration count. A delta holds its whole product from its enumeration until it parks it compact at the end of its preparation, inside its slot (`fold::Prepared::park`), so the slot counts that whole product beyond the compact one it parks. A delta traces only the windows default's memo cannot answer, so it costs less than a configuration enumerated from scratch (SCRATCH_PEAK_BYTES). Its `--cache-stats` lines show how much: `shared_memo_hits` counts the windows it takes from that memo, and its trace_cache length counts the windows its own memo holds of those it traces beyond it. A delta's trace memo is held to `fixpoint::TABLE_TRACE_CEILING`, so that length leaves out the windows with a concrete third slot and a known fourth, which the memo does not store, and those a shard forgot to make room, which its `memo_clears` line's `trace=` counts the evictions of; it is the count the delta traces only in a build with `--no-trace-memo-skip` at a `--trace-memo-buckets=` no shard reaches. The counts that follow are readings of a crate whose trace memos had no ceiling, where each length is that traced count: at 45 runes default traces 32.38M windows itself, and beyond its memo `ss03` traces 14.76M, `ss03+ss05` 14.06M, `ss08` 8.89M, `ss05` 5.03M and `ss04` 4.03M, each taking 14.49M to 17.14M windows from it. What a slot costs depends on what runs beside it, so the term is set against the whole wave, not against one configuration built alone. The readings are the memory series' line for one bare `build-tables` over every settlement configuration at 45 runes (`make memory-series ARGS='--holder tables 45 --interval 0.2 --kernel-threads <W> --scratch-beside-default <S>'`), its outputs through FIFOs and to the null device, with `--cache-stats` and `--timings` and KERNEL_ALLOCATOR_ENVIRONMENT set, sampled every 0.2 s, on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), one line at a time: three runs at each width from one to six with every delta over default's memo, and three runs six wide with two, three, four and five deltas from scratch (`var/keep/ram-inventory/v4/change8-readings/`, the `wave-w<W>-r<n>.txt` and `scratch-s<S>-r<n>.txt` lines). Each run's whole-build peak is the higher of its resident and footprint lines, because the resident line under-reads a build by whatever the memory compressor has taken from the process and the footprint leaves out the pages the allocator retains; it is the resident line in every run here, and no run swapped. It reads 7.93 GB one wide, 8.02 to 8.05 GB two wide, 8.13 to 8.14 GB three wide, 8.25 to 8.26 GB four wide, 8.35 to 8.38 GB five wide and 8.38 to 8.39 GB six wide, and six wide 8.42 to 8.48 GB with two from scratch, 9.10 to 9.11 GB with three, 9.11 to 9.12 GB with four and 11.55 to 11.59 GB with five. At 45 runes the whole-build peak comes at the rendezvous for every count below three from scratch and at the start from three up. Below three, the highest resident line any configuration records before it parks reads 3.36 GB one wide, 4.21 to 4.29 GB two wide, 4.48 GB three wide, 5.13 to 5.15 GB four wide, 5.89 to 5.91 GB five wide, 5.49 to 5.51 GB six wide and 7.10 to 7.13 GB with two from scratch, the `[c] <config> resident_parked` line, once every configuration has parked, reads 0.96 to 1.28 GB, and the peak sample falls 7 to 45 s before the build ends, in the cross-configuration exchange, its footprint 0.83 to 1.14 GB below its resident line; READ_BACK_FOLD_BYTES books that peak. From three up, the peak is the release point of the deltas started from scratch, the highest resident line before parking within 0.01 GB of it and the footprint 0.17 to 0.18 GB below it, which the start books (SCRATCH_PEAK_BYTES, UNCAPPED_SCRATCH_PEAK_BYTES). No series line records the wave's peak before every configuration has parked, so no reading measures this term on its own, and since the rendezvous and the start cover every reading, `table_build_booking_bytes` covers every whole-build peak above at any value of this term: the covering rule does not bound it. The recorded resident lines before parking set a floor instead: the wave's booking covers each width's highest such line only with this term at 0.85 GB or more, two wide binding, or 0.9 GB rounded up to the tenth. The value is 1.4 GB, above that floor and below the 1.9 GB a slot from scratch books (SCRATCH_PEAK_BYTES less PARKED_FOLD_BYTES), which a delta over default's memo stays under: it is FOLD_PREPARATION_BYTES's, the 1.4 GB issue #618's change 8 projects for a slot that holds a whole product before it parks, because a width at or below the delta count books the preparation's slot as a delta's, which covers the preparation only while that term is at most this one (`rebuild/test_memory_budget.py` checks it). At 1.4 GB the booking covers every reading by 0.74 GB or more: the rendezvous's 9.0 GB one to four wide, the wave's 9.6 GB five wide and 11.0 GB six wide, and the wave's 12.0, 12.5, 13.0 and 13.5 GB with two to five from scratch, where it binds over the start. It errs high on purpose: a per-unit estimate that is too low puts the machine into swap, while one that is too high only narrows the wave. With the other terms as they stand, a value of 7.37 GB or more costs the 18-core machine its whole wave under a gated cycle, with gate:make-test's pytest pool running, 7.55 GB or more costs the 12-core machine its whole wave under a gated cycle, and 7.91 GB or more costs both machines their whole wave alone; `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` check those widths. The same build takes 154.8 to 175.7 s six wide, 157.6 to 179.4 s five wide, 166.3 to 177.6 s four wide, 183.6 to 187.6 s three wide, 217.5 to 250.1 s two wide and 305.0 to 348.0 s one wide. On both fleet machines every run_m1 starts every delta from scratch beside default, which SCRATCH_PEAK_BYTES books, so no run_m1 step peak in `rebuild/out/cycle-timings.ndjson` measures this term. Re-measure this constant, FOLD_PREPARATION_BYTES, SCRATCH_PEAK_BYTES, UNCAPPED_SCRATCH_PEAK_BYTES, DEFAULT_MEMO_BYTES, PARKED_FOLD_BYTES and READ_BACK_FOLD_BYTES together, and TABLE_BUILD_PEAK_BYTES from them, whenever the alphabet grows or a per-configuration working set changes, on a 48 GiB machine with no other build running, one series line at a time with KERNEL_ALLOCATOR_ENVIRONMENT set, at least three runs each: the whole-wave build the readings above describe, at one wide through the configuration count with every delta over default's memo and at the configuration count with each count of deltas from scratch that whole tiers reach (`--scratch-beside-default <S>`, `fanout::scratch_tiers`), whose `parked_heap` lines give PARKED_FOLD_BYTES, whose count that starts every delta reads SCRATCH_PEAK_BYTES and whose counts below it check UNCAPPED_SCRATCH_PEAK_BYTES net of a SCRATCH_PEAK_BYTES for each delta from scratch; then each settlement configuration alone at one wide (`--configs <config> --kernel-threads 1`), since the highest reading is not always the same configuration's (SCRATCH_PEAK_BYTES), `default`'s resident lines through its fold among them (FOLD_PREPARATION_BYTES); and `default` alone again with `--no-trace-memo-skip` at a `--trace-memo-buckets=` no shard reaches (UNCAPPED_SCRATCH_PEAK_BYTES). Of the whole-wave builds, a run whose peak falls after every configuration has parked, later than its `resident_parked` line and above every resident line before it, is a READ_BACK_FOLD_BYTES reading, and one whose peak falls at its start is the start's. This term and FOLD_PREPARATION_BYTES are each the smallest value, rounded up to the tenth, at which `table_build_booking_bytes` covers every run's whole-build peak; where the rendezvous and the start already cover every reading, as at 45 runes, that leaves them unbounded, and each is set at or above the floor the recorded resident lines before parking set, 1.4 GB over a 0.9 GB floor at 45 runes, with FOLD_PREPARATION_BYTES at most this term.
DELTA_SLOT_BYTES = 1_400_000_000
# What the slot that runs only default's fold preparation holds beyond default's parked product: the last term of `table_build_booking_bytes` at a width above the delta count. Default's preparation (its expansion, row chains and first rules) takes a worker slot when the delta wave starts (`fanout::run_configs_tables`). Above the delta count that slot runs nothing else; at or below it, a delta takes the slot when the preparation ends, so there the slot is booked as a delta's. The preparation consumes default's enumeration product and holds its prepared product whole until it parks it compact at its end, inside the slot (`fold::Prepared::park`), so beyond the parked product PARKED_FOLD_BYTES counts, the slot holds up to that whole product. The enumeration frees its worklist's stack, its item table, its pending rows and its row log's base index before its memo release and holds its rows in a row log whose drain frees one input's chunks at a time (SCRATCH_PEAK_BYTES), and the partition check that runs between the sort and the preparation holds one base's claims at a time (TABLE_BUILD_PEAK_BYTES). No series line records the slot's peak inside the preparation and its parking. One bare `build-tables` of `default` alone at one wide at 45 runes, through the harness and on the machine DELTA_SLOT_BYTES's readings use, three runs (`var/keep/ram-inventory/v4/change8-readings/`, the `solo-default-r<n>.txt` lines), peaks at 1.99 GB at the release point, before the preparation starts, and its resident lines from there read 0.42 GB after the release, 1.03 GB after the sort and 0.36 to 0.37 GB once the product is parked compact. Only a width above the delta count books this term, and in DELTA_SLOT_BYTES's readings the rendezvous and the start cover every whole-build peak at any value of it, so the covering rule does not bound it. The recorded resident lines before parking set a floor: 1.03 GB after the sort, less PARKED_FOLD_BYTES, is 0.83 GB, or 0.9 GB rounded up to the tenth. The value is the 1.4 GB issue #618's change 8 projects for the preparation, above that floor and above what default's own `read_back_heap` line alone, 1.19 GB, puts the whole product and the spec at. It is at most DELTA_SLOT_BYTES, which `rebuild/test_memory_budget.py` checks, so a width at or below the delta count, where the preparation and the deltas share the slots, is booked at its deltas alone. `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` check the count of deltas from scratch at the shipped value. In every reading DELTA_SLOT_BYTES cites with fewer than three deltas from scratch, default's whole fold takes 12.4 to 18.5 s and the build peaks 38.8 s or more after default's enumeration ends, so the preparation is over before the build peaks, and with three or more the build peaks 4.3 to 15.3 s before default's enumeration ends, before the preparation starts, so this term errs high.
FOLD_PREPARATION_BYTES = 1_400_000_000
# The peak of one configuration enumerated from scratch and alone, through its fold: what a slot holds, with its parked product, in a build where every configuration enumerates from scratch (`table_build_booking_bytes` with `from_scratch`): a narrowed `rebuild/tools/scratch_build.py` set without `default`, or `default_memo_sharing=False`. A build that includes `default` and at least one delta enumerates `default` from scratch before the wave, alone or beside the deltas `deltas_from_scratch` starts from scratch with it, and each of those runs under this bound: `table_build_booking_bytes` books each of them at one such bound in that start and at this bound less its parked product in the wave. `default` runs under this bound only in a start where every delta starts from scratch, and the booking then charges it one SCRATCH_PEAK_BYTES; in a start with fewer it keeps its trace memo uncapped for the deltas that read it and runs under UNCAPPED_SCRATCH_PEAK_BYTES, which the booking charges it instead. The whole-process reading is one slot's cost, with no separate allowance for the process: what the slots share is the parsed spec and its index, which the process holds in about 0.1 GB before a configuration's engine is built. An enumeration's engine keeps its trace memo and its transition map in sixteen shards each (`hash::ShardedMap`; `var/keep/issue-535/` holds the readings behind `hash::SHARD_BITS`), holds its prospect memo to 2^20 buckets and its candidate memo to 2^22 (`fixpoint::TABLE_MEMO_CEILINGS`), clearing each in place when it fills that table and deriving a dropped entry again when it is next asked for, holds its trace memo to 2^25 buckets, clearing a shard whose next insert would double its table, and stores none of the windows with a concrete third slot and a known fourth (`fixpoint::TABLE_TRACE_CEILING`), settling a forgotten window again when it is next asked for, and records its rows in a row log (`fixpoint::RowLog`): one chain of 64 KiB chunks an input at twelve bytes a row, a twenty-four-byte header for each fragment of one base's rows, and a base index of seventeen bytes a bucket from each base to its first fragment, which its `[c] <config> row_log bytes= fragments= revisited_bases=` line reports. Its pending class-grain rows are forty-eight bytes of narrowed ids with no index of their own, its worklist's stack holds a four-byte id an item, and its drain decodes one input's chain at a time into the row vector, sorting that input's rows and freeing the chain. So of what it holds only the rows grow with the windows it settles once the trace memo reaches its ceiling, and below the ceiling a narrower memo key or entry lowers this bound by as much as it shrinks the trace memo. `default` holds its trace memo to no ceiling while a delta is left to read it, which UNCAPPED_SCRATCH_PEAK_BYTES books. The reading is one bare `build-tables` of each settlement configuration alone at one wide, from scratch through its fold (`--configs=<config> --threads=1`), at 45 runes, through the release binary under `/usr/bin/time -l` with `--cache-stats`, `--timings` and KERNEL_ALLOCATOR_ENVIRONMENT set and its payloads linked to the null device, one build at a time on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), five runs each (`var/keep/ram-inventory/v4/change6-reading/runs.ndjson`, the `solo-<config>` arms). Its resident peak reads 2.004 to 2.005 GB for `ss03+ss05`, 2.004 GB for `ss03`, 1.993 to 1.994 GB for `default`, 1.993 GB for `ss08`, 1.992 GB for `ss05` and 1.968 to 1.969 GB for `ss04`, each configuration's five runs within 0.001 GB of each other and each run's peak footprint 0.03 to 0.04 GB below. The peak is the release point, one 16 KiB page above what the `resident_before_release` line reads, in every run. Each configuration holds 20.07M (`ss04`) to 20.68M (`ss03`) windows in a trace memo of 2^25 buckets, well below the 29,360,128 its ceiling holds, so no shard evicts, clears its prospect memo 22 times and its candidate memo 5 times, and its `resident_after_sort` line reads 1.01 to 1.04 GB, so the drain of the rows, their sort and the fold stay well below the peak. The constant is the highest reading, 2.005 GB for `ss03+ss05`, rounded up to the tenth. With the allocator's large cache on, the same solo builds on a crate that also wrote memo files read 2.97 to 3.11 GB above the same builds with it off, chiefly earlier tables of the memos that grew (`var/keep/issue-537/`; `var/keep/issue-519/` lists them). The deltas started from scratch beside default peak together with default near the end of their enumerations, which is why the start books every engine in it at its own bound at once: this bound for each delta from scratch, and for `default` this bound with every delta from scratch or UNCAPPED_SCRATCH_PEAK_BYTES with fewer. The reading is the same harness's build over every settlement configuration six wide (`--threads=6 --scratch-beside-default=<S>`) with three deltas from scratch and with five, five runs each (the `w6s3-default` and `w6s5-default` arms). The crate starts whole tiers of equal unlocking-rune count (`fanout::scratch_tiers`): at 45 runes the worklist's tiers unlock 5, 5, 2, 1 and 1 runes, so two starts `ss03` and `ss03+ss05`, three adds `ss08`, four starts the same three, and five starts every delta. The higher of each run's resident and footprint lines, its resident line in every run, reads 9.06 to 9.12 GB with three and 11.45 to 11.66 GB with five, its footprint 0.16 to 0.18 GB below it, and no run swapped. Only the start with every delta from scratch, divided by its configuration count, reads this term: at most 1.94 GB a configuration, below the solo readings. A start with fewer reads UNCAPPED_SCRATCH_PEAK_BYTES plus a SCRATCH_PEAK_BYTES for each delta from scratch, which that term's comment checks. The count trades memory and CPU for wall time: three from scratch takes 133.95 to 134.79 s and 482.1 to 484.7 CPU-s, and five 106.44 to 107.49 s and 616.4 to 622.8 CPU-s, at peak footprints of 8.89 to 8.96 and 11.27 to 11.49 GB. Every delta from scratch takes 26.5 to 28.4 s off the build against three, for 131.7 to 140.7 CPU-s and 2.31 to 2.60 GB more peak footprint, so `deltas_from_scratch` starts as many as the booking fits. The tables are the same bytes at every count and ceiling: each configuration's digest is the same in every build over the whole set, and `default`'s in every build of it alone at every ceiling and under either eviction, at 45 and at 38 runes. With the other terms as they stand, a value of 6.81 GB or more leaves the 18-core machine under a gated cycle, with gate:make-test's pytest pool running, fewer than every delta from scratch, 6.96 GB or more leaves the 12-core machine under a gated cycle fewer, and 7.26 GB or more leaves either machine alone fewer. At this value both machines start every delta from scratch beside default, alone and under a gated cycle; `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` check that count in each of those cases. Re-measure it with DELTA_SLOT_BYTES.
SCRATCH_PEAK_BYTES = 2_100_000_000
# The peak of `default` enumerated from scratch while it keeps every window its trace memo settles for the deltas that read it: what `table_build_booking_bytes` books for `default` in the start of a build that starts some deltas from scratch beside it but fewer than there are deltas, where each of those books SCRATCH_PEAK_BYTES. A trace memo another enumeration reads has no ceiling, since that reader's hits on a memo that forgets would be lost (`fixpoint::MemoAccess` in the crate), so this is SCRATCH_PEAK_BYTES' engine with a trace memo that grows with every window it settles and stores the windows with a concrete third slot and a known fourth too. When every delta starts from scratch no delta reads `default`'s memo, and `default` books SCRATCH_PEAK_BYTES as they do. The reading is one bare `build-tables` of `default` alone at one wide with its trace memo at a ceiling no shard reaches and storing every window (`--configs=default --threads=1 --trace-memo-buckets=67108864 --no-trace-memo-skip`), at 45 runes, through the harness and on the machine SCRATCH_PEAK_BYTES's reading uses, five runs (`var/keep/ram-inventory/v4/change6-reading/runs.ndjson`, the `solo-default-uncapped` arm): its resident peak reads 3.239 GB in all five, each run's peak footprint 0.03 GB below, at the release point, with 32.38M windows in a trace memo of 2^26 buckets, where the same build at the ceiling holds 20.55M. The constant is that reading rounded up to the tenth. The start with three deltas from scratch checks it: its `default` holds the same 32.38M windows, and the build reads 9.06 to 9.12 GB, 0.48 GB or more under the 9.6 GB this term and three SCRATCH_PEAK_BYTES book, so net of those three its highest reading leaves 2.82 GB, under this term. Neither fleet machine books it, since both start every delta from scratch, so no value of it moves their count.
UNCAPPED_SCRATCH_PEAK_BYTES = 3_300_000_000
# The memory of the default memo snapshot shared by the delta wave, the first term of `table_build_booking_bytes`. `fanout::run_configs_tables` keeps default's whole snapshot alive while a delta is left to read it, which is whenever fewer deltas start from scratch than there are deltas; when every delta starts from scratch, default keeps none (`fanout::default_access`). `memo::SnapshotEntries` holds thirty-six-byte key/entry records and a thirty-two-byte block of hash tags per sixteen records, two bytes a record, with the pools beside them and no hash-table slack. At 45 runes default's `--cache-stats` trace_cache length, 32,375,967 windows in every reading in `var/keep/ram-inventory/v4/change7-readings/`, puts one snapshot's records and blocks at 1.23 GB, and the memos hold no elimination text. Where some delta reads the snapshot, it also carries the liveness verdicts default published for those deltas (`fanout::run_configs_tables`), whose record table, slots and fired deltas `--cache-stats` reports as `published_verdicts`: 0.13 GB at every width in the whole-wave readings DELTA_SLOT_BYTES cites, where every delta reads the snapshot, 0.11 GB with two deltas from scratch and 0.09 GB with three or four in that folder's `scratch-s<S>-r<n>.txt` lines, and nothing with five, where no delta reads it. The constant is one such snapshot and the highest of those verdicts, 1.36 GB, rounded up to the tenth.
DEFAULT_MEMO_BYTES = 1_400_000_000
# What one configuration's prepared fold product holds while it waits parked (`fold::Parked`, `fanout::run_configs_tables`): its class rows coded against the previous row in blocks of about 4 KiB that end at near-slot run boundaries, each with a 27-byte index entry, its label-grain expansion as one joint bit a row, and its row chains as stretches of rows that share a parent and a chain length, nine bytes a stretch, beside its window options, its first rules, its source-row index, its label pool, whose texts are `Rc`s, and its outcome, settled and provenance tables, which stay as they are, from the end of its preparation, inside its slot, until every configuration has parked. Every configuration's parked product is resident at once at the end of the wave, so a wave that reuses a worker slot holds the finished configurations' products beside the deltas still enumerating; `table_build_booking_bytes` books one per configuration, and DELTA_SLOT_BYTES and FOLD_PREPARATION_BYTES each count a slot beyond its own configuration's product. Once every configuration has parked, each reads its product back whole for the cross-configuration exchange, which READ_BACK_FOLD_BYTES books. With `--cache-stats` each configuration's `[c] <config> parked bytes= class_rows= fold_rows= stretches=` line gives what its coded forms hold and how many class rows, label-grain rows and stretches they code, so a build reads off its parked bytes a class row. The reading is `build-tables` with `--cache-stats`, whose `[c] <config> parked_heap` line gives the bytes the process's malloc zones hold allocated at the rendezvous before any product is read back, where every configuration's parked product and the parsed spec are all the process holds (`fixpoint::heap_bytes`, from `/usr/bin/heap`). The constant is the highest reading divided by the count of settlement configurations that reading built, the spec's share included, rounded up to the tenth. In the whole-wave readings DELTA_SLOT_BYTES cites, the line reads 0.612 to 0.614 GB over six configurations at every width and every count from scratch, 0.10 GB a configuration, and 0.097 to 0.106 GB for each configuration built alone, so the constant is 0.2 GB. Of that, each configuration's `parked` line reads 0.056 to 0.057 GB of coded forms, 3.64 to 3.68 bytes a class row over 15.28M to 15.55M class rows, 20.38M to 22.31M label-grain rows and 0.46M to 0.51M stretches, and the rest is the part of the product that stays as it is and the spec's share. With the other terms as they stand, no value below SCRATCH_PEAK_BYTES, which `rebuild/test_memory_budget.py` holds it under, costs either fleet machine its whole wave or a delta from scratch, alone or under a gated cycle. Re-measure it with DELTA_SLOT_BYTES whenever the alphabet grows or what a parked product holds changes.
PARKED_FOLD_BYTES = 200_000_000
# What one configuration's prepared fold product holds once it is read back (`fold::Parked::unpark`): the class rows, their label-grain expansion, whose row holds pool ids rather than strings (`fold::FoldRow`, sixteen bytes) and is allocated at its exact length, the row chains, the window options, its first rules and its source-row index, from the rendezvous through the cross-configuration exchange (`rebuild/kernel-rs/src/crossconfig.rs`) until the configuration finishes. Every configuration reads its product back once every configuration has parked, which is after the last delta has enumerated, whatever the width, so the rendezvous holds every configuration's product read back at once and nothing that enumerates: `table_build_booking_bytes` books this term once per configuration as the rendezvous and books no width below it, so a narrow `AMS_KERNEL_THREADS` books no less than the rendezvous holds. Each configuration's share of what the exchange and the allocator hold beside the products is booked here too. The reading is the whole build's peak where it falls after every configuration has parked, the higher of its resident and footprint lines, in the whole-wave readings DELTA_SLOT_BYTES cites: at 45 runes that is every width with every delta over default's memo and the build with two deltas from scratch, while with three or more the build peaks at its start (DELTA_SLOT_BYTES says how the two are told apart). It reads 7.93 GB one wide to 8.39 GB six wide and 8.42 to 8.48 GB with two from scratch, its resident line in every run. The constant is the highest reading, 8.48 GB, divided by the six settlement configurations that build holds, 1.41 GB, rounded up to the tenth. The floor it rests on is the `[c] <config> read_back_heap` line `--cache-stats` prints: the bytes the process's malloc zones hold allocated once every configuration has read its product back and before the exchange's first round, where those products and the parsed spec are all the process holds (`fixpoint::heap_bytes`, from `/usr/bin/heap`). It reads 7.09 GB over six configurations at every width and every count from scratch, 1.18 GB a configuration, and 1.14 to 1.21 GB for each configuration built alone. The rest of the peak is the exchange's working set beside the products and the allocator's retained pages: the peak footprint of those builds reads 7.09 to 7.34 GB, and the resident line 0.83 to 1.14 GB above it. With the other terms as they stand, a value of 6.81 GB or more leaves the 18-core machine under a gated cycle, with gate:make-test's pytest pool running, no width whose booking fits, which costs it its whole wave and every delta from scratch, 6.96 GB or more does the same to the 12-core machine under a gated cycle, and 7.26 GB or more to both machines alone. Re-measure it with DELTA_SLOT_BYTES whenever the alphabet grows or what a prepared product holds changes.
READ_BACK_FOLD_BYTES = 1_500_000_000
# The peak `table_build_booking_bytes` books for the one `build-tables` process at the configuration count with every delta started from scratch beside default: the width and the count `kernel_threads_default` and `deltas_from_scratch` give a bare run_m1 on the 48 GiB machines (`doc/fleet.md`). A gated cycle there runs the same width and count, since gate:make-test's pytest pool still leaves room for every delta from scratch (SCRATCH_PEAK_BYTES), so it books the same. It is the highest of the wave, DEFAULT_MEMO_BYTES plus PARKED_FOLD_BYTES per configuration plus SCRATCH_PEAK_BYTES less PARKED_FOLD_BYTES for each delta from scratch plus DELTA_SLOT_BYTES for each other delta plus FOLD_PREPARATION_BYTES, the start, a SCRATCH_PEAK_BYTES for each delta from scratch and, for default, UNCAPPED_SCRATCH_PEAK_BYTES while a delta is left to read its memo or one more SCRATCH_PEAK_BYTES once none is, and the rendezvous, READ_BACK_FOLD_BYTES per configuration; with every delta from scratch the wave's 13.5 GB binds over the start's 12.6 GB and the rendezvous's 9.0 GB. `rebuild/test_memory_budget.py` checks that it equals that booking, so it moves whenever a term is re-measured. `make job-costs` compares every run_m1 step since its commit with it, and an overrun means a build outgrew what its width books, so the kernel constants need re-measuring. Its reading is the higher of two lines: the step's peak, which is the build process's resident line, and the build's peak footprint, which `build_table_files` reads between the process's exit and its reap and run_m1 writes on its `[t] kernel_build_tables` line. The resident line leaves out what the memory compressor holds of the build, so the row prints both lines, and an overrun the compressor's share makes can be told apart from a build that grew. A record without a footprint, which includes every build on Linux, where an exited process's footprint cannot be read, reads its resident line alone. At 45 runes the whole-build peak comes at the rendezvous for every count below three from scratch and at the start from three up (DELTA_SLOT_BYTES). Every reading in `var/keep/ram-inventory/v4/change8-readings/` is inside it, the highest, 11.59 GB with every delta from scratch, by 1.91 GB, and every reading of a build over the whole set there is inside the booking at its own width and count, by 0.74 GB or more: 11.55 to 11.59 GB with five from scratch, booked at 13.5 GB, 9.10 to 9.12 GB with three and four, booked at 12.5 and 13.0 GB, 8.42 to 8.48 GB with two, booked at 12.0 GB, and 7.93 to 8.39 GB with none, booked at 9.0 GB one to four wide, 9.6 GB five wide and 11.0 GB six wide. Building a snapshot is inside this bound. `Engine::take_memo` releases the other live memos, then drains the trace map into an array one shard at a time, holding the array beside the shards not yet drained and freeing each shard's table once its entries have moved, so the map is gone before partitioning. Partitioning holds a four-byte source position per record and the offset cursors, plus a temporary sort buffer for an unusually large collision bucket, and frees the positions before it builds the tag blocks. Enumeration and the fold still hold corpus-sized state. An enumeration frees its worklist's stack, its item table, its pending class-grain rows and its row log's base index once its member cross-check has appended the class rows to its row log (after its `--cache-stats` lines, when they are asked for), ahead of its memo release, so everything from there to its return (its snapshot build, its drain, its sort and its partition check) runs without them, and the partition check holds the member claims of one base at a time (`fixpoint::DeepPartitionCheck`). Every engine of the build, `default`'s, each delta's and each delta's started from scratch, holds its prospect and candidate memos to the ceilings SCRATCH_PEAK_BYTES describes (`fixpoint::TABLE_MEMO_CEILINGS`), its rows in a row log, its pending class-grain rows as narrowed ids and its worklist's stack as item ids, and every reading the terms rest on is of that crate.
TABLE_BUILD_PEAK_BYTES = 13_500_000_000
# The peak of one configuration's length-4 string replay; `run_m1.run_replay_strings` divides the machine's memory by it for its width. It covers the engine's trace memo over the windows its texts reach, with no liveness probes beyond the prospect's own and no ranking (`Replay::new` turns it off), plus the window memo's inverse label map and block buffer when the walk writes its dump. The replay's engine keeps its prospect and candidate memos unbounded, because only the table fixpoint sets `EngineModes::memo_ceilings`, so its candidate memo can outgrow the table build's capped one; the constant rests on its own readings below, not on SCRATCH_PEAK_BYTES. Nothing is subtracted before the division: the replay starts after the `build-tables` process has exited, so no configuration's memo is alive, and each worker loads one settlement TSV and builds its own engine. With ·He in the alphabet, the trace key packed onto ordinals, the trace entry at sixteen bytes, no ranking and KERNEL_ALLOCATOR_ENVIRONMENT set, one `replay-strings` over `default` alone at `--threads=1` with `--memo-dir` on (the shipped path, since a full replay always writes its dump) and `--cache-stats` peaks at 0.82 GB in five runs under `/usr/bin/time -l` on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), its `peak memory footprint` line 0.01 GB below, and walks 2,141,490 texts over 3,634,484 windows in 9.1 to 9.9 s. Its trace memo holds 6,843,578 entries in a table that holds 7,340,032 before it doubles, its walk memo 3,634,484 in one that holds 3,670,016, and its candidate memo 3,560,380 in one of the same size. Each grows into its table through doublings, and the next doubling of any of them raises the peak. The same walk over every settlement configuration at the configuration count peaks at 3.58 to 4.04 GB for the whole process in five runs, 0.72 to 0.81 GB per worker, its footprint 0.05 GB below, with no swaps and 0.38 to 0.42 s of system time against 54.4 to 55.9 s of user time; the whole-process figure moves from run to run, presumably with how the five walks' doublings overlap. Each configuration's `[t] replay[<config>]` line reads 10.7 to 11.3 s against the solo 9.1 to 9.9 s. The five walks share the machine's memory without paging; paging would show in the system time and in a footprint above the resident figure, and neither rises here. In the same alternating rounds with the allocator's large cache on, the solo walk peaks at 1.24 GB, where its `resident_after_walk` line also reads 1.24 GB against 0.68 GB with the setting, because the allocator keeps the tables the memos outgrew, and the wide walk peaks at 6.11 GB, 1.22 GB per worker, with 0.39 to 0.55 s of system time. The counts and the window memo dumps are the same bytes either way, and the `[t] replay[<config>]` lines read 9.2 to 9.6 s solo and 10.9 to 11.6 s wide. The same divisor sizes the settle-memo absorbs that follow a full replay, since `run_m1.run_replay_strings` runs their pool at the replay's width. One absorb of `default`'s dump (`conform.absorb_replay_memo`, 3,634,484 rows) peaks at 0.33 GB in its own process in three runs, a fraction of the divisor. The logs are under `var/keep/issue-536/`. The constant takes the higher of the solo reading and the wide run's per-worker figure, adds a quarter and rounds up to the tenth. It errs high for the reason DELTA_SLOT_BYTES gives, and the headroom also covers neighbors no term subtracts: the replay shares run_m1's process tree with the glyph chain, the window packers and the shipped-order walkers, each a small, flat working set beside it (`run_m1._core_bound_threads` sizes the last two). The criterion is that both fleet machines (`doc/fleet.md`) replay every settlement configuration in one round, alone and in a gated cycle with gate:make-test's pytest pool running; `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` check it. The replay-walk row of `make job-costs` checks it against each walk's share of the crate's peak (`run_m1._record_replay_walk`). Re-measure it whenever the alphabet grows, `REPLAY_MAX_LENGTH` changes or the trace memo's key or entry changes shape, with KERNEL_ALLOCATOR_ENVIRONMENT set: the two runs above, both with the memo dump on, and the absorb over the solo run's dump. If a per-configuration line gets longer in the wide run while the system time rises, the width is causing paging.
REPLAY_PEAK_BYTES = 1_100_000_000


def table_build_booking_bytes(
    width: int,
    *,
    configs: int,
    from_scratch: bool = False,
    scratch_beside_default: int = 0,
) -> int:
    """The memory a table build of `configs` configurations at `width` worker slots is booked at, which `kernel_threads_default` fits to the machine and `TABLE_BUILD_PEAK_BYTES` states at the configuration count. Every build books `DEFAULT_MEMO_BYTES` for `default`'s memo snapshot and `PARKED_FOLD_BYTES` for each configuration, whose prepared fold product waits parked and compact from the end of its preparation until every configuration has parked. A build that shares `default`'s memo books `DELTA_SLOT_BYTES` for each delta in flight, at most `configs - 1`, and above that delta count `FOLD_PREPARATION_BYTES` for the slot that runs only `default`'s fold preparation; a slot beyond the configuration count holds nothing. Both slot terms count only what a slot holds beyond its own configuration's parked product. With `scratch_beside_default`, capped as the crate caps it at the delta count and one less than `width`, that many of the deltas enumerate from scratch beside `default` instead (`fanout::run_configs_tables`), and each books `SCRATCH_PEAK_BYTES` less its parked product in place of a delta's slot. Such a build holds `default`'s live engine and theirs at their from-scratch peaks together before any delta starts, which the wave's terms do not cover, so its booking is the higher of that start and the wave. The start books one `SCRATCH_PEAK_BYTES` for each of them and, for `default`, `UNCAPPED_SCRATCH_PEAK_BYTES` while fewer of them start from scratch than there are deltas, since `default` then keeps every window its trace memo settles for the deltas that read it, or one more `SCRATCH_PEAK_BYTES` once every delta starts from scratch and `default`'s trace memo is held to its ceiling like theirs. With `from_scratch`, for a build in which every configuration enumerates from scratch (a set without `default`, or `default_memo_sharing=False`), each slot up to the configuration count books `SCRATCH_PEAK_BYTES` less its parked product instead, and `scratch_beside_default` adds nothing, because such a build has no `default` to start beside.

    Once every configuration has parked, which is after the last delta has enumerated, each reads its product back whole and holds it through the exchange, whatever the width. So every build is booked at no less than that rendezvous, `READ_BACK_FOLD_BYTES` for each configuration, and its booking is the higher of the rendezvous and the wave, or the highest of the rendezvous, the start and the wave; without it, a narrow width, a stated `AMS_KERNEL_THREADS` among them, would book below what the rendezvous holds.
    """
    fixed = DEFAULT_MEMO_BYTES + configs * PARKED_FOLD_BYTES
    rendezvous = configs * READ_BACK_FOLD_BYTES
    if from_scratch:
        return max(fixed + min(width, configs) * (SCRATCH_PEAK_BYTES - PARKED_FOLD_BYTES), rendezvous)
    deltas = configs - 1
    scratch = max(0, min(scratch_beside_default, deltas, width - 1))
    slots = (
        scratch * (SCRATCH_PEAK_BYTES - PARKED_FOLD_BYTES)
        + min(width - scratch, deltas - scratch) * DELTA_SLOT_BYTES
        + (FOLD_PREPARATION_BYTES if width > deltas else 0)
    )
    wave = fixed + slots
    if not scratch:
        return max(wave, rendezvous)
    lead = SCRATCH_PEAK_BYTES if scratch == deltas else UNCAPPED_SCRATCH_PEAK_BYTES
    return max(wave, lead + scratch * SCRATCH_PEAK_BYTES, rendezvous)


def kernel_threads_default(
    *,
    configs: int,
    coresident_bytes: float = 0,
    total_bytes: int | None = None,
    from_scratch: bool = False,
) -> int:
    """The number of table-build worker slots this machine has memory for, for a build of `configs` configurations: the widest width up to `configs` whose `table_build_booking_bytes` fits the machine's memory, less the reserve `memory_budget` states and less `coresident_bytes`, floored at one. `AMS_KERNEL_THREADS` overrides the arithmetic whenever it is set. Each slot runs one delta configuration, and `default`'s fold preparation takes one of them when the wave starts. So the width is `configs` when the whole wave fits, with `default`'s preparation in a slot of its own; otherwise it is `memory_budget.how_many_fit` over `DELTA_SLOT_BYTES`, with `DEFAULT_MEMO_BYTES` and a `PARKED_FOLD_BYTES` per configuration taken off first, capped at the delta count, where the preparation's slot passes to a delta once the preparation ends. `from_scratch` is for a build in which every configuration enumerates from scratch (a set without `default`, which `run_m1.build_tables` passes it for, or `default_memo_sharing=False`): its divisor is `SCRATCH_PEAK_BYTES` less `PARKED_FOLD_BYTES`, capped at `configs`. A stated `AMS_KERNEL_THREADS` below the configuration count still parks every configuration's product and reads every one back at the rendezvous, so a width of one does not bound the peak near one delta. The rendezvous (`READ_BACK_FOLD_BYTES` for each configuration) is the same at every width, so a machine whose memory it does not fit fits no width's booking, and the division still gives that machine the widest width whose wave fits below the configuration count, since a narrower one holds the same rendezvous.

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


def deltas_from_scratch(
    width: int,
    *,
    configs: int,
    coresident_bytes: float = 0,
    total_bytes: int | None = None,
    from_scratch: bool = False,
) -> int:
    """At most how many of the deltas a table build of `configs` configurations at `width` worker slots enumerates from scratch beside `default`, the `scratch_beside_default` `run_m1.build_tables` passes `build_table_files`. The crate takes them heaviest first (`fanout::delta_worklist`), so the count is all this decides: it is the largest count, up to the delta count and one less than `width`, at which the build's booking (`table_build_booking_bytes`) fits the machine's memory, less the reserve `memory_budget` states and less `coresident_bytes`. Every count is tried, not only those below the first that misses, because the booking can fall at the last one: while a delta is left to read `default`'s memo, `default`'s start books `UNCAPPED_SCRATCH_PEAK_BYTES`, and with every delta from scratch it books one `SCRATCH_PEAK_BYTES` instead, so every delta can fit where one fewer does not. The crate starts them only in whole tiers of equal unlocking-rune count up to that bound (`fanout::scratch_tiers`), because a delta left tied with one taken still runs after `default` as long as the one taken would have, while `default` enumerates more slowly beside them; where it starts fewer, the booking at this count errs high. The width is decided first and never narrowed by this, so a machine whose width memory sets runs every delta over `default`'s memo unless what that width leaves over holds the from-scratch peaks. The count trades memory and CPU for wall time, since a delta enumerated from scratch traces all of its windows instead of waiting for `default`'s memo, and memory alone decides it (`SCRATCH_PEAK_BYTES` gives the measured trade). A stated width is judged the same way. A from-scratch build has nothing to start beside `default`, so it answers zero. `coresident_bytes` and `total_bytes` mean what they mean for `kernel_threads_default`."""
    if from_scratch:
        return 0
    total = memory_budget.total_memory_bytes() if total_bytes is None else int(total_bytes)
    usable = total - memory_budget.os_reserve_bytes(total_bytes=total) - max(0, int(coresident_bytes))
    return max(
        (
            count
            for count in range(min(configs - 1, width - 1) + 1)
            if table_build_booking_bytes(width, configs=configs, scratch_beside_default=count) <= usable
        ),
        default=0,
    )


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


# How long a crate call that answers in seconds or minutes at any alphabet size is waited for before `_run_kernel` kills it: `enumerate-configs`, which only the tests run, `settle-cases`, `guard-sweep`, `replay-emitted`, `cargo build`, and the first run after it. A limit is there to stop a crate that has hung, not to bound a slow one, and a call it kills records nothing. So `build-tables` and `replay-strings`, whose time grows with the alphabet, derive their limits from it at the call (`build_tables_timeout`, `replay_timeout`), and neither waits less than this.
TIMEOUT = 1800
# The factor between the duration `build_tables_timeout` or `replay_timeout` scales from a measurement and the limit it returns. It covers what the scaling leaves out: the 12-core M4 Pro Mac mini (`doc/fleet.md`), which has no measured duration; a call that shares its cores with another pool; and for the replay, the growth in what a text costs as the tables grow, which the full-alphabet projection puts at up to 1.3 times. A limit set too low kills a healthy call and loses its whole work, while one set too high only delays the error a hung crate gets, so the margin errs high.
KERNEL_TIMEOUT_MARGIN = 3
# The alphabet the two measured durations below were taken over: the 44-rune alphabet's 34 letters and three boundary tokens (`labels.spec_alphabet`).
MEASURED_SYMBOLS = 37
# The slowest whole-set table build at MEASURED_SYMBOLS: one `build-tables` over every settlement configuration one wide, from scratch, on a crate that also wrote each configuration's memo file, 432.2 s on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), the slowest of that alphabet's whole-wave readings (`var/keep/issue-511/way2/`). Every wider build of the same alphabet took less.
BUILD_TABLES_SECONDS = 432.2
# How the table build's time grows with the alphabet: as the symbol count to this power, the number of slots in a window (its input, its left and four rights). It gives the 35th, 36th and 37th symbols 19.0%, 18.4% and 17.9% more time, against the 16.4%, 15.5–16.3% and 18.8% that fresh builds of the same code grew by when ·Jay, ·Ye and ·Way were added, and from 37 symbols to the full alphabet's 47 it gives 4.2 times, above the 3.6 to 3.8 times that is the pessimistic projection of that growth.
BUILD_TABLES_GROWTH = 6
# What one text costs the replay at MEASURED_SYMBOLS: the slowest of five walks over every text at maximum length 5, one configuration each, run at once in one `replay-strings` process at the deep replay's memo ceiling on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), 71,270,177 texts in 542.3 s (`var/keep/issue-495/replay/replay-deep-all.log`). The build's own replay at maximum length 4 costs less a text: 11.3 s for 2,141,490 texts at most, in REPLAY_PEAK_BYTES's wide run with ·He in the alphabet.
REPLAY_TEXT_SECONDS = 542.3 / 71_270_177
# On macOS, where cargo copies the binary into target/release instead of hard-linking it, every `cargo build` replaces it there (it removes the file, then copies the new one in) even when nothing recompiled. So a build in one process can make another process's exec miss the file for an instant, and can end the first-exec scan of the file it removes: syspolicyd holds a new file's first run until its scan ends, and a scan that finds the path gone ends in a Gatekeeper termination of every process waiting on it, each an exit of -9 with empty stderr. This lock orders them: a build holds it exclusively for the whole `cargo build` and the new binary's first run (`_finish_first_exec_scan`), so no scan is in flight when the next build starts, and an invocation holds it shared for the spawn only, never for the run. `cargo_build` is how every build in the repository takes it, `make kernel-build` included; a `cargo build` typed by hand takes no lock.
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
# The stamp for a window enumeration that is not kept. `build-tables` always writes the windows payload, whose head is where a caller reads the rules and cells back from, and always requires a stamp for that head. A caller with no fingerprint over the rune files has no stamp to give, so it passes this word and keeps no payload: `run_m1.build_tables` keeps the head from its FIFO and drops the rest as it arrives, and this module's `build_tables` reads a payload file in a scratch directory it deletes, so the word never reaches an artifact.
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


def cargo_build(*, capture: bool = True) -> None:
    """Build the kernel in release mode, holding the binary relink lock exclusively through the build and the new binary's first run. Checking that the binary exists is not enough, because a stale binary sits at the same path as a fresh one; building makes sure the sources on disk are what runs. A warm build takes a fraction of a second; `ensure_built` runs it once per process. With `capture` false, cargo writes to this process's stdout and stderr as it runs, and a failure's exception quotes none of it."""
    arguments = ["cargo", "build", "--release", "--manifest-path", str(MANIFEST)]
    try:
        with _relink_lock(fcntl.LOCK_EX):
            finished = subprocess.run(arguments, capture_output=capture, timeout=TIMEOUT)
            if finished.returncode == 0:
                _finish_first_exec_scan()
    except FileNotFoundError:
        raise KernelBuildError(
            "no cargo on PATH — install the Rust toolchain (https://rustup.rs) to build the M1 kernel"
        ) from None
    except subprocess.TimeoutExpired:
        raise KernelBuildError(
            f"cargo gave no answer within {TIMEOUT} seconds on {' '.join(arguments)}"
        ) from None
    if finished.returncode != 0:
        if not capture:
            raise KernelBuildError(f"the kernel did not build (cargo exited {finished.returncode})")
        errors = finished.stderr.decode(errors="replace").strip().split("\n")
        tail = "\n".join(errors[-BUILD_TAIL_LINES:])
        raise KernelBuildError(f"the kernel did not build (cargo exited {finished.returncode}):\n{tail}")


def _finish_first_exec_scan() -> None:
    """Run the new binary with no arguments, to which the crate answers with its usage and exit 2, and wait for it to exit. The process cannot exit before the file's first-exec scan ends, so the caller, which holds the relink lock exclusively, keeps every other build from replacing the file during the scan (`LOCK_PATH`). Raises `KernelRunError` for a missing binary, a run that has not answered within `TIMEOUT`, or any other exit."""
    arguments = [str(BINARY)]
    try:
        finished = subprocess.run(arguments, capture_output=True, timeout=TIMEOUT)
    except FileNotFoundError:
        raise _no_binary() from None
    except subprocess.TimeoutExpired:
        raise _no_answer(arguments, "its first run", TIMEOUT) from None
    if finished.returncode != 2:
        errors = finished.stderr.decode(errors="replace").strip()
        raise KernelRunError(f"the kernel exited {finished.returncode} on its first run: {errors}")


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


def _spawn_kernel(
    arguments: list[str], *, stdin: bool, env: Mapping[str, str] | None = None
) -> subprocess.Popen:
    """Spawn the binary with the relink lock held shared for the spawn only, the moment a concurrent `cargo build` could make the path disappear, so the caller waits without the lock and a long enumeration never blocks a build elsewhere. With `stdin`, the child's stdin is a pipe; without it, the child inherits this process's stdin. With `env`, the child runs with that environment instead of this process's. Raises `KernelRunError` for a missing binary."""
    try:
        with _relink_lock(fcntl.LOCK_SH):
            return subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE if stdin else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
    except FileNotFoundError:
        raise _no_binary() from None


def _no_binary() -> KernelRunError:
    return KernelRunError(
        f"no kernel binary at {BINARY} — run `make kernel-build` first, or let the caller's cargo_build() build it"
    )


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


def _allocator_environment() -> dict[str, str] | None:
    """The environment `build_table_files` and `replay_strings` spawn their crate process with: on macOS this process's environment and `KERNEL_ALLOCATOR_ENVIRONMENT`, elsewhere None, which leaves the child this process's own."""
    return {**os.environ, **KERNEL_ALLOCATOR_ENVIRONMENT} if sys.platform == "darwin" else None


def _run_kernel_reaped(
    arguments: list[str], verb: str, *, timeout: float, env: Mapping[str, str] | None = None
) -> tuple[subprocess.CompletedProcess, ReapedPeaks]:
    """`_run_kernel` with no stdin, returning beside the result the child process's own resident peak and peak footprint in bytes (`peak_rss.reap_peaks_bytes`), either None where it cannot be read. `communicate` reaps the child with `waitpid`, which discards its resource usage and its footprint, so this drains each pipe on a thread of its own and, once both are closed, waits for the child's exit, reads its peak footprint and reaps it with `os.wait4`. The crate starts no process of its own, so both figures are the crate's. `env` is the child's environment, as `_spawn_kernel` takes it. Raises `KernelRunError` for a missing binary, or for a timeout after killing the child. An interrupt while it waits kills and reaps the child before it propagates, so no crate outlives its caller's cleanup, which for `build_table_files` includes ending each read of a payload FIFO (`stream_payloads`)."""
    process = _spawn_kernel(arguments, stdin=False, env=env)
    assert process.stdout is not None and process.stderr is not None
    drained: dict[str, bytes] = {}

    def drain(name: str, stream: IO[bytes]) -> None:
        with stream:
            drained[name] = stream.read()

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    deadline = time.monotonic() + timeout
    try:
        for reader in readers:
            reader.start()
        for reader in readers:
            reader.join(max(0.0, deadline - time.monotonic()))
    except BaseException:
        process.kill()
        process.wait()
        raise
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
    """The semantics tokens a stamp over this process's enumeration must include, in stamp order: the simulated prospect, the shifted follower prefer slots and the class-grain deep slots, each present only while it is on. Each changes settlement semantics or enumeration grain without changing a hashed source, so a key over the sources alone would treat an enumeration made with a flag on as current in a process with it off, and the reverse. `run_m1.tables_inputs` appends these to the tables' stamp, `run_m1.memo_structure_stamp` includes them in the table build record's structure stamp, `run_m1.locality_lines` in the string replay's stamp, and `artifact_cycle.deep_replay_structure_stamp` in the deep replay's, so all four agree."""
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
    scratch_beside_default: int = 0,
    verdict_sharing: bool = True,
) -> TableBuild:
    """Every named configuration folded in the crate: its settlement TSV, its join TSV, its plain window enumeration stamped `inputs`, and the table digest of the pair, returned as `TableBuild.digests` (`{config: digest}`) beside the `[t]` lines `--timings` made the crate write.

    This is `enumerate-configs` plus the fold in one process, so there is no stream between them: the fold runs on the product the worklist still holds. The windows payload is written uncompressed because the crate depends only on serde_json. The crate opens its path (`windows_payload`) with `File::create`, so the caller decides where the payload goes by what it puts at that path before the call: `run_m1.build_tables`, which packs it into the `.gz` artifact, and `rebuild/tools/scaling_sweep.py`, which counts it, a FIFO each reads it through (`stream_payloads`); a reading that wants no payload, a symbolic link to the null device; and a caller that wants the file, such as this module's `build_tables` and the tests, nothing.

    One process handles every named configuration because the configurations after `default` are enumerated as deltas over it. `default` enumerates first and keeps its trace memo. Each other configuration reads that memo for every window whose key names none of its own unlocking runes and whose settlement read none of them, and settles only the rest, `threads` worker slots at a time, with `default`'s fold preparation in one of them. `rebuild/kernel-rs/src/memo.rs` gives the argument; the window locality rule, applied across configurations, makes the shared results exact. `default_memo_sharing=False` enumerates every configuration from scratch, and `rebuild/test_kernel_exec.py` checks that it writes the same tables as the delta build. `scratch_beside_default` starts up to that many of the heaviest deltas from scratch beside `default` instead, in whole tiers of equal unlocking-rune count, which writes the same tables and holds what `table_build_booking_bytes` books for it (`deltas_from_scratch` decides it); when every delta starts that way, `default` keeps no memo. When some delta reads `default`'s memo, `default` also publishes its deep-slot liveness verdicts beside it, and a delta served one reruns only the probe's asks that name one of its own unlocking runes (`rebuild/kernel-rs/src/liveness.rs`); `verdict_sharing=False` runs every probe whole, and `rebuild/test_kernel_exec.py` checks that it writes the same tables.

    The memo lives only inside the process: the build reads no memo file and writes none, so every build enumerates cold.

    `symbols` is the size of the spec's alphabet (`labels.spec_alphabet`), which sets how long the call is waited for (`build_tables_timeout`).

    The process runs through `_run_kernel_reaped`, which reads its peak footprint after it exits and before it is reaped, so `TableBuild.peaks` carries both its resident peak and its peak footprint. The resident line leaves out what the memory compressor holds of the build, and the kernel-build row of `make job-costs` reads the higher of the two (`TABLE_BUILD_PEAK_BYTES`). On macOS it runs with this process's environment and `KERNEL_ALLOCATOR_ENVIRONMENT` (`_allocator_environment`), so the tables a memo outgrows go back to the system as they are freed.

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
    if scratch_beside_default:
        arguments.append(f"--scratch-beside-default={scratch_beside_default}")
    if timings:
        arguments.append("--timings")
    finished, peaks = _run_kernel_reaped(
        arguments, "build-tables", timeout=build_tables_timeout(symbols), env=_allocator_environment()
    )
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


def windows_payload(out_dir: Path, config: str) -> Path:
    """Where `build-tables` writes one configuration's plain window payload under `out_dir` (`build_table_files` says what a caller may put there first)."""
    return Path(out_dir) / f"windows-{config}.tsv"


def payload_head(head: bytes, path: Path) -> io.StringIO:
    """A payload's head line, as a reader of its FIFO kept it, as the open text stream `table.read_windows` reads with `windows=False`, named for the payload's path so that a refusal names it."""
    stream = io.StringIO(head.decode("utf-8"))
    stream.name = str(path)
    return stream


class _PayloadFifo[T]:
    """A FIFO made at a crate payload path and one daemon thread reading it. The thread's open waits for the crate's `File::create` (an open of a FIFO with `O_TRUNC` truncates nothing), it runs `read` over the stream, and the crate's close of the file gives it end of file at once. It closes its descriptor before it reports, so a reader that fails breaks the crate's pipe at the crate's next write instead of leaving it to fill. The thread is a daemon so that a reader an interrupted `finish` leaves waiting never holds the interpreter open."""

    def __init__(self, path: Path, read: Callable[[IO[bytes]], T]) -> None:
        path.unlink(missing_ok=True)
        os.mkfifo(path)
        self.path = path
        self._read = read
        self._result: Future[T] = Future()
        threading.Thread(target=self._run, name=f"payload {path.name}", daemon=True).start()

    def _run(self) -> None:
        try:
            with self.path.open("rb") as source:
                value = self._read(source)
        except BaseException as error:
            self._result.set_exception(error)
        else:
            self._result.set_result(value)

    def finish(self) -> T:
        """Open and close the FIFO for writing without blocking, once the thread has it open or has finished, so a thread whose crate never opened it reads an empty stream instead of waiting forever; then wait for the thread, remove the FIFO, and return what `read` returned or raise what it raised. The open fails with ENXIO while no reader has the FIFO open, which here means only that the thread has not reached its own open yet, so it is retried. A close that lands while the thread is still inside its open can leave the thread waiting in its first read on macOS, so the open and close repeat until the thread has finished; an extra writer that writes nothing changes no byte the thread reads. Call it once the crate has exited."""
        try:
            while not self._result.done():
                try:
                    os.close(os.open(self.path, os.O_WRONLY | os.O_NONBLOCK))
                except OSError as error:
                    if error.errno != errno.ENXIO:
                        raise
                wait([self._result], timeout=0.01)
            return self._result.result()
        finally:
            self.path.unlink(missing_ok=True)


def _finish_fifos[T](fifos: Mapping[str, _PayloadFifo[T]]) -> tuple[dict[str, T], Exception | None]:
    """Finish every reader, each one even after another has failed, so none is left waiting, and return what they read beside the first failure among them."""
    values: dict[str, T] = {}
    failure: Exception | None = None
    for name, fifo in fifos.items():
        try:
            values[name] = fifo.finish()
        except Exception as error:
            if failure is None:
                failure = error
    return values, failure


_STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


@contextlib.contextmanager
def _stop_signals_interrupt() -> Iterator[None]:
    """Make SIGINT, SIGTERM and SIGHUP raise `KeyboardInterrupt` in the main thread for the length of the block, where each would otherwise end the process at once or raise it there anyway. A crate's FIFOs need that: a stop that reaches only this process, such as the SIGTERM the artifact cycle's driver sends a step's `uv run`, which passes it on, would otherwise end it with no cleanup and leave the crate running, to block for good in its open of a FIFO no one reads. Only a signal at its default action or at Python's own SIGINT handler is taken over: one that is ignored stays ignored, as under `nohup`, and a handler a caller installed, such as `artifact_cycle.stop_signals`', keeps doing what it does. Only the first of them raises and later ones are dropped until the block ends, because a stop sent to the process group arrives again as `uv run` forwards it and must not cut short the cleanup the first started. Only the main thread can install handlers, so on any other thread this installs none. The previous handlers are restored on exit."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    caught: list[int] = []

    def stop(signum: int, _frame: object) -> None:
        if caught:
            return
        caught.append(signum)
        raise KeyboardInterrupt(signal.Signals(signum).name)

    previous = {}
    for signum in _STOP_SIGNALS:
        handler = signal.getsignal(signum)
        if handler in (signal.SIG_DFL, signal.default_int_handler):
            previous[signum] = handler
            signal.signal(signum, stop)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def stream_payloads[R, T](
    paths: Mapping[str, Path], read: Callable[[str, IO[bytes]], T], run: Callable[[], R]
) -> tuple[R, dict[str, T]]:
    """Run `run`, which runs a crate process that writes a payload at each of `paths`, with a FIFO at each path and a thread reading it as `read(name, stream)`, so each payload goes from the crate to its reader without reaching the disk, and return what `run` returned beside what each reader returned, keyed as `paths` is. However `run` ends, every reader is finished (`_PayloadFifo.finish`) and every FIFO removed, so a FIFO the crate never opened gives its reader an empty stream and holds nothing. A reader's failure is raised after a clean `run`, and otherwise set as the cause of `run`'s own failure, since a reader that stops early breaks the crate's pipe; an interrupt is raised as it came. `run` must not end while its crate still runs, so on an interrupt it kills and reaps the crate first, as `_run_kernel_reaped` and `rebuild/tools/scaling_sweep.py`'s `run_size` do.

    Called on the main thread, it makes a SIGTERM or SIGHUP that would end the process at once raise an interrupt instead for its length (`_stop_signals_interrupt`), so a stop that reaches only this process still kills and reaps the crate and removes every FIFO. A SIGKILL skips all of this: the crate it leaves running blocks for good in its open of a FIFO left at a path it had not yet opened, or writes into the FIFO the next build makes there.
    """
    fifos: dict[str, _PayloadFifo[T]] = {}
    with _stop_signals_interrupt():
        try:
            try:
                for name, path in paths.items():
                    fifos[name] = _PayloadFifo(path, functools.partial(read, name))
                ran = run()
            except BaseException as error:
                _values, failure = _finish_fifos(fifos)
                if failure is not None and isinstance(error, Exception):
                    raise error from failure
                raise
            values, failure = _finish_fifos(fifos)
            if failure is not None:
                raise failure
            return ran, values
        finally:
            for path in paths.values():
                path.unlink(missing_ok=True)


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
    """Replay every named configuration's persisted rules under `out_dir` over every text up to `max_length`, and return `{config: {texts, windows, skipped}}` on a clean walk. With `families`, only the texts naming one of those runes are walked. With `last`, one symbol of the spec's alphabet (`labels.spec_alphabet`), only the texts ending in it are walked, as in the deep sweep's units (`run_m1_gates.sweep_units`): the walks over every symbol partition the texts between them, and `skipped` counts only the texts ending in it that `families` leaves out. It reaches the subcommand as `--last=` and the symbol's code point, and a string that is not one character raises `ValueError` before anything is spawned, as does `last` beside `memo_dir`, since a memo written from one symbol's texts would hold only part of a configuration's windows. Rules apply first-match with the settled left fed forward, and each window is checked against the crate's own settlement. `windows` counts window settles: each distinct window once on a walk with no ceiling, and again each time it is met after a release. The spec is the same memoized dump the settlement functions and the guard sweep read.

    A disagreement or a refused window raises `ReplayDisagreement` with the crate's message. Every other failure the CLI contract distinguishes raises `KernelRunError`, as does a clean exit whose output names a different set of configurations from the one requested. An empty `families` raises `ValueError` before anything is spawned, because the subcommand treats it as a usage error and a caller with nothing to walk has nothing to ask.

    With `memo_dir`, each passing walk writes its window memo to `replay_memo_dump(memo_dir, config)`: every distinct window it settled, keyed in the crate's own form, with every distinct settled record beside them. The result is unchanged. The build asks for one on a full replay, so the settle memo every later phase loads is filled here instead of by the oracle. The deep walk (`rebuild/tools/deep_replay.py`) never asks for a memo and passes `memo_windows` instead, which reaches the subcommand as `--memo-windows=`: the most windows a walk keeps memoized before it releases its memos and continues. Passing both raises `ValueError` before anything is spawned, because a walk that released its memo holds only the windows settled since, and a walk that writes its memo has no ceiling. A ceiling below one window also raises.

    `on_peak`, when given, is called with the crate process's own peak RSS in bytes once a walk has answered cleanly (`_run_kernel_reaped`), before this returns. The walks are threads of that one process, so the figure covers every walk it ran at once. The build's replay (`run_m1.run_replay_strings`) and the deep replay (`rebuild/tools/deep_replay.py`) record it for `make job-costs`. On macOS the process runs with this process's environment and `KERNEL_ALLOCATOR_ENVIRONMENT` (`_allocator_environment`), so the tables the trace memo and the walk memo outgrow go back to the system as they are freed.
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
    finished, peaks = _run_kernel_reaped(
        arguments, "replay-strings", timeout=timeout, env=_allocator_environment()
    )
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
        raise _no_binary() from None
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
        with windows_payload(tables, config).open("rt", encoding="utf-8") as handle:
            _stamp, decision = table.read_windows(handle)
        return decision, table.read_join_tsv(tables / f"joins-{config}.tsv")


def main() -> None:
    """`make kernel-build`: build the kernel with cargo's own output on the terminal, so an author sees every diagnostic, and report a failed build in one line instead of a traceback."""
    try:
        cargo_build(capture=False)
    except (KernelBuildError, KernelRunError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
