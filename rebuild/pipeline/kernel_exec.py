"""The Python side of the Rust kernel boundary. It builds the binary, runs the table build and the stream fan-out, reads the section 5.7 guard verdicts, settles batched windows for explain, review, conform and the tests, runs the string and shipped-order replays, and returns single-configuration products and tables to callers other than `run_m1`. It lives in the pipeline because the pipeline calls it. The `rebuild/tools/kernel_*.py` scripts are separate measurement harnesses.

The semantics defaults live here beside the flags that pass them to the kernel. `SIMULATED_PROSPECT_DEFAULT` and `VOTE_SLOTS_DEFAULT` control settlement, and `DEEP_CLASSES_DEFAULT` controls enumeration. Each is a module attribute read at call time, so a process sets them through the environment and a test can monkeypatch them. A caller that wants a different world builds a `SettlementModes` and passes it to `settle_cases`, `settle_windows` or `settle_sequences`.

The build is `cargo build --release` against the crate's manifest. Release is the only profile anything in the repository runs: the pipeline and the spec-echo test in `rebuild/test_kernel_io.py` both run `target/release/ams-m1-kernel`, and a debug binary is too slow to substitute for it. A machine without `cargo` gets a `KernelBuildError` that says how to install it. `ensure_built` builds once per process, and every caller in the process shares that build.

`build_table_files` is what `run_m1` calls. One `build-tables` process enumerates every settlement configuration and folds each in place. It writes each configuration's settlement TSV, treaty TSV and plain window enumeration, and returns the contract digest of each configuration's pair of tables. No transition stream is written, because the fold reads the product the worklist still holds; that saves writing, reading and parsing several hundred megabytes per configuration. The configurations after `default` are deltas over it. `default` enumerates first and keeps its trace memo, and each other configuration reads that memo for every window whose key names none of its own unlocking runes and whose settlement read none of them, and traces only the rest. The memo is also carried across builds in the `memo-<config>.tsv.gz` files packed beside the tables. The next build reuses a memoized window only if its settlement read no rune whose content has changed and no predicate class whose membership has changed. `rebuild/kernel-rs/src/memo.rs` gives the argument, and `run_m1.memo_seed` decides which files may be read.

`enumerate_configs` is the stream fan-out. One process enumerates every named configuration and writes each one's transition stream to its own file. Nothing on the build's path calls it; `enumerate_transitions` and the tests do.

The table build's width is limited by memory, because a live configuration holds its whole working set until it has emitted. `DELTA_PEAK_BYTES` is the peak of one delta. `KERNEL_THREADS_DEFAULT` is `memory_budget.how_many_fit` over it: the machine's memory, less the reserve that policy states, less `DEFAULT_MEMO_BYTES` for the `default` memo snapshots kept alive for the wave, divided by one delta and floored at one. It is computed once at import. A larger machine, or a container limited by its cgroup, gets its own width without editing a constant. `AMS_KERNEL_THREADS` overrides the arithmetic in either direction. The artifacts are byte-identical at any width, so the override changes only memory use. Callers cap the width at the number of settlement configurations (`conform.SETTLEMENT_CONFIGS`; the overlay configuration is never enumerated) and at the usable cores. The string replay after a build has its own divisor, `REPLAY_PEAK_BYTES`. `replay_threads_default` divides by it with nothing subtracted first, because the replay's engines are built after the build process has exited and no memo outlives it. `AMS_REPLAY_THREADS` overrides it.

`build_tables` and `enumerate_transitions` are the single-configuration forms, and neither writes anything that outlives the call. Each dumps one spec to a scratch directory. `build_tables` runs `build-tables` into that directory and reads the two tables back through `table.read_windows` and `table.read_treaty_tsv`. `enumerate_transitions` enumerates the raw product as a stream and parses it into a `table.FixpointProduct`. Only tests call either.

`guard_sweep` runs `guard-sweep` once and returns the complete mapping from `(ligature, first raw slot, second raw slot)` to the configuration-independent formation verdict. It is memoized per spec identity, so a process sweeps a spec once however many callers ask. `guard_sweep_under` returns the same mapping for one named configuration instead of quantifying over the powerset. It is not memoized, because only tests read it; they compare each configuration's verdicts with the quantified ones.

The settlement and replay functions share the memoized spec dump. `replay_strings` is the enumeration-completeness check `run_m1.run_replay_strings` runs after every table build. One `replay-strings` process reads the settlement TSVs the build wrote and walks every text of the string universe, or only the texts naming the families the caller says changed, checking each window against the crate's own settlement. A disagreement raises `ReplayDisagreement` with the crate's message, which names the configuration, the window and the text. `settle_cases` is the raw form. It writes one tab-separated question line per independent window (`case_line` writes one) and returns the full Rust trace for each. It checks the answer count and that each answer line starts with its question verbatim, and decodes each distinct result once however many windows returned it. `settle_windows` asks the crate for the settled record alone (seven tab-separated fields under `--settled-only`) and decodes each straight to a `Settled`. The conform walker uses it because it needs only outcomes, not traces. Like `settle_sequences`, it takes an `on_error` argument, so a caller prefilling windows it may never read can get `None` for a refused window and keep the rest of the batch. `settle_sequences` is what explain, the probe and the review surface call. The subcommand takes independent windows, but a sequence's next left context is the previous window's result, so a batch of sequences advances in waves: every sequence's first position, then every second position using the first wave's results. Boundary positions are settled locally because their results are model constants. `settle_codepoints` settles one text. The CLI writes boundary tokens as `edge`, `space`, `zwnj`, `namer-dot` and `unknown`. The guard parser converts them to the `RightToken` constants in `settle`, so consumers never confuse these model tokens with glyph names such as `uni200C` or `periodcentered`.

The codecs between the transport lines and the pipeline's model types live here too, because every settlement caller needs them: `case_line` for questions, and `trace_of` and `_settled_of_fields` for answers. A window the crate refuses returns `{raise, message}`. That becomes a `settle.SettleError` carrying the crate's error code as its bucket and its message verbatim, so a caller can sort refusals without parsing text. Any other malformed answer means the boundary itself is wrong and raises `KernelRunError`.

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
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rebuild.pipeline import kernel_io, settle, table
from rebuild.pipeline.model import CellId, Provenance, ResolvedSpec, Settled, feature_config_token
from rebuild.pipeline.table import DecisionTable, FixpointProduct, TreatyTable
from rebuild.tools import memory_budget

REPO_ROOT = Path(__file__).resolve().parents[2]
BINARY = REPO_ROOT / "rebuild" / "kernel-rs" / "target" / "release" / "ams-m1-kernel"
MANIFEST = REPO_ROOT / "rebuild" / "kernel-rs" / "Cargo.toml"
# The peak of one delta configuration through enumeration and its memo write; the table build's width divides the machine's memory by it. A delta that shares nothing with default's memo traces the whole configuration, so the bound is a configuration enumerated from scratch, and the worst delta sets it, not default. Measured with ·Ye in the alphabet, the trace, prospect and candidates keys packed onto ordinals, and the trace entry at sixteen bytes (`rebuild/kernel-rs/src/engine.rs` asserts each size): the worst delta enumerated from scratch and alone is `ss03+ss05`, at `--threads=1`, tracing every window itself, with its memo write at the release point and its fold included. Under `/usr/bin/time -l` on the 48 GiB machine it peaks at 6.23 GB and 6.14 GB for the whole process over two runs. `ss05` reads 6.12 and 6.17 GB, `ss03` 5.52 GB and `ss04` 5.35 GB, and every run's `peak memory footprint` line matches its resident figure. The 32 GiB machine has no solo reading with ·Ye in the alphabet. The whole-process reading is used as one worker slot's cost, with no separate allowance for the process: what the slots share is the parsed spec and its index, which `spec_parse` reports at 0.0 s and which the process holds in about 0.1 GB before a configuration's engine is built. The constant rounds the highest reading up to the tenth. The peak is at the release point, with every memo table built, and in the memo write that follows it. The `resident_before_release` census line reads 6.06 and 6.14 GB for `ss03+ss05` and 6.03 and 6.12 GB for `ss05`, within 0.2 GB of each run's peak, and equals the peak for `ss03` and `ss04`. `resident_after_sort` reads 2.2 to 4.7 GB, so the drain of the transitions, their sort and the fold stay below the peak, and a narrower memo key or entry lowers this divisor by as much as it shrinks the tables. In a build, no live delta traces from scratch. Each runs over default's memo, and its `--cache-census` lines show how much: `memo_base_hits` counts the windows it takes from that memo, and its trace_cache length counts what it traces beyond it. Only `config_seed=False` enumerates a live configuration from scratch. In one `build-tables` over every settlement configuration on the 48 GiB machine, with each delta's memo file written at its release point and no previous-build seed, `ss03` and `ss03+ss05` each trace 18.37M windows beyond default's memo against the 25.27M they trace alone, `ss05` 4.96M and `ss04` 3.62M. That process peaks at 18.02 GB four wide (DEFAULT_MEMO_BYTES plus about 3.9 GB per worker slot) and at 15.87 GB three wide. On the 32 GiB machine, with ·Jay the newest letter, the same build four wide peaks at 19.24 GB, and at 20.37 GB seeded from a previous build's memo files. Each of those wave readings has a `peak memory footprint` line within two gigabytes of its resident figure, on either side (18.77 GB beside the 18.02, 15.10 GB beside the 15.87, 19.06 GB beside the 19.24 and 19.58 GB beside the 20.37). That margin is the condition for using a resident figure at all, because the resident line under-reads a wave by whatever the memory compressor has taken from the process. The same shape of build under forty-byte trace keys, on a pass under memory pressure, read 14.91 GB and 15.92 GB four wide against footprints of 21.08 GB and 21.92 GB; a reading whose footprint is gigabytes above its resident line measures the compressor, not the build. The divisor budgets each worker slot at its bound, not at what the wave's slots measured, and it errs high: a per-unit estimate that is too low puts the machine into swap, while one that is too high only narrows the wave. DELTA_PEAK_BYTES and DEFAULT_MEMO_BYTES are chosen together so that the 48 GiB machine (`doc/fleet.md`) runs the whole delta wave at once, every configuration after default in one round, both alone and in a gated cycle with gate:make-test's pytest pool running. The 32 GiB machine fits three worker slots, one fewer than the four deltas, alone and gated alike, so its wave takes a second round. Held to three at once, a from-scratch wave on the 48 GiB machine takes 245.7 s against 198.9 s at four. `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` check both machines' widths. The gated case sets an upper limit on this divisor: with the pytest pool running, a divisor above 7.75 GB leaves the 32 GiB machine two worker slots instead of three, and one above 10.11 GB leaves the 48 GiB machine three instead of four. Re-measure the two constants together, never one alone, whenever the alphabet grows or a per-configuration working set changes. The recipe is the solo reading above plus the wave measurement, both from scratch and seeded from a previous build's memo files (what every artifact-cycle pass after the first runs), since the seeded build holds a second default snapshot during the wave (DEFAULT_MEMO_BYTES says which). The wave measurement is one `build-tables` over every settlement configuration with memo output, `--cache-census` and `/usr/bin/time -l`, counted only where the footprint line is within two gigabytes of the resident one. With ·Ye in the alphabet, neither machine has such a reading of the seeded build.
DELTA_PEAK_BYTES = 6_300_000_000
# The memory of the default memo snapshots shared by the delta wave, subtracted from the machine's memory before dividing by DELTA_PEAK_BYTES. `fanout::run_configs_tables` keeps the fresh default snapshot and, when present, the previous build's default seed alive together. Loading a seed drops keys naming edited runes, and lookup and memo writing apply the full read-journal exclusion. `memo::SnapshotEntries` holds thirty-six-byte key/entry records and a four-byte offset per hash-prefix bucket, with the pools beside them and no hash-table slack. With ·Ye in the alphabet, default's `--cache-census` entry count puts its records and offsets at 0.90 GB, against 1.24 GB for a hash table at that census's capacity. A seeded build can hold two default-sized snapshots, so one snapshot's saving is not the whole shared term. On the 32 GiB machine, all settlement configurations four wide with memo writes measure 12.46 GB maximum resident memory / 15.49 GB peak memory footprint cold, and 10.92 / 14.95 GB seeded with ·Ye invalidated, under `/usr/bin/time -l`. The corresponding hash-map runs measure 11.73 / 14.62 GB and 11.98 / 16.39 GB, and the table, window and uncompressed memo bytes agree in both. These footprint-to-resident gaps exceed the two-gigabyte margin DELTA_PEAK_BYTES requires. The 48 GiB machine's one compact-snapshot reading, the cold four-wide wave DELTA_PEAK_BYTES cites, is within the margin but cannot separate the shared snapshots from the delta builds, and neither machine has a compact-snapshot reading of a seeded build within the margin. So the 2.5 GB allowance stays conservative and does not widen the pool. Re-measure it and TABLE_BUILD_PEAK_BYTES together with DELTA_PEAK_BYTES under that constant's whole-wave criterion; a smaller snapshot alone does not show a smaller process peak.
DEFAULT_MEMO_BYTES = 2_500_000_000
# The peak of the one `build-tables` process: the shared default snapshots plus every delta build in flight, bounded by DEFAULT_MEMO_BYTES plus DELTA_PEAK_BYTES times the width. Building a snapshot is inside this bound. `Engine::take_memo` releases the other live memos, then collects the trace map into an array, holding both during the collection, and drops the map before partitioning. Partitioning holds a four-byte source position per record and the offset cursors, plus a temporary sort buffer for an unusually large collision bucket. `read_memo` reserves space from a count of the TSV's window lines, stores accepted records directly, and boxes the compacted array after indexing, with no growing hash table. Enumeration, the memo writer and the fold still hold corpus-sized state. With ·Ye on the 32 GiB machine, four-wide measurements under `/usr/bin/time -l` with memo writes and `--cache-census` give 12.46 GB resident / 15.49 GB footprint cold, and 10.92 / 14.95 GB with ·Ye invalidated in a previous-build seed. Kernel wall-clock times are 352.20 s and 356.42 s, against 338.08 s and 330.79 s for hash-map snapshots on the same spec. The cold peak is higher despite the smaller snapshot, and the seeded footprint is lower. The resident readings fail DELTA_PEAK_BYTES's two-gigabyte margin, so they do not justify lowering the 22 GB bound, which also covers the 20.37 GB seeded reading that meets the margin and overlap that depends on scheduling. A smaller bound needs a direct compact-snapshot measurement of the 48 GiB machine's five-wide wave. `make job-costs` compares the cycle's sampled run_m1 process peak with this figure. The delta width is still set by DELTA_PEAK_BYTES and DEFAULT_MEMO_BYTES.
TABLE_BUILD_PEAK_BYTES = 22_000_000_000
# The peak of one configuration's horizon-4 string replay; `run_m1.run_replay_strings` divides the machine's memory by it for its width. It covers the engine's trace memo over the windows the string universe's texts reach, with no liveness probes beyond the prospect's own and no explain ladder (`Replay::new` turns it off), plus the window memo's inverse label map and block buffer when the walk writes its dump. That is a subset of what the enumeration's engine holds, so it is a fraction of DELTA_PEAK_BYTES and measured separately. Nothing is subtracted before the division: the replay starts after the `build-tables` process has exited, so no configuration's memo is alive, and each worker loads one settlement TSV and builds its own engine. With ·Ye in the alphabet, the trace key packed onto ordinals, the trace entry at sixteen bytes and no explain ladder, one `replay-strings` over `default` alone at `--threads=1` with `--memo-dir` on (the shipped path, since a whole-universe walk always writes its dump) peaks at 1.24 GB under `/usr/bin/time -l` on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), with its `peak memory footprint` line the same, and walks 1,727,604 texts over 2,909,666 windows in 7.4 s. The same walk over every settlement configuration at the configuration count peaks at 6.10 GB for the whole process, 1.22 GB per worker, with no swaps, the footprint equal to the resident figure, and 0.31 s of system time against 45.4 s of user time. Each configuration's `[t] replay[<config>]` line reads 8.9 to 9.3 s against the solo 7.4 s. The five walks share the machine's memory without paging; paging would show in the system time and in a footprint above the resident figure, and neither rises here. The same divisor sizes the settle-memo absorbs that follow a whole-universe walk, since `run_m1.run_replay_strings` runs their pool at the replay's width. One absorb of `default`'s dump (`conform.absorb_replay_memo`, 2,909,666 rows) peaks at 0.25 GB in its own process, a fraction of the divisor. The constant takes the higher of the solo reading and the wide run's per-worker figure, adds a quarter and rounds up to the tenth. It errs high for the reason DELTA_PEAK_BYTES gives, and the headroom also covers neighbors no term subtracts: the replay shares run_m1's process tree with the glyph chain, the window packers and the shipped-order walkers, each a small, flat working set beside it (`run_m1._core_bound_threads` sizes the last two). The criterion is that both fleet machines (`doc/fleet.md`) replay every settlement configuration in one round, alone and in a gated cycle with gate:make-test's pytest pool running; `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` check it. Re-measure it whenever the alphabet grows, `REPLAY_HORIZON` changes or the trace memo's key or entry changes shape: the two runs above, both with the memo dump on, and the absorb over the solo run's dump. If a per-configuration line gets longer in the wide run while the system time rises, the width is causing paging.
REPLAY_PEAK_BYTES = 1_600_000_000


def kernel_threads_default(*, coresident_bytes: float = 0, total_bytes: int | None = None) -> int:
    """The number of table-build worker slots this machine has memory for beside `default`'s memo. Each slot runs one delta configuration, and `default`'s fold takes one of them. `AMS_KERNEL_THREADS` overrides the arithmetic whenever it is set. Otherwise `memory_budget.how_many_fit` divides the machine's memory, less the reserve that policy states and less `DEFAULT_MEMO_BYTES`, by `DELTA_PEAK_BYTES`, floored at one. `DEFAULT_MEMO_BYTES` covers the fresh default snapshot plus its filtered previous-build seed when present. Each snapshot holds compact immutable records, their hash-prefix offsets and the pools the records index, not a configuration through enumeration, which is why the two terms are different numbers.

    A stated width is floored at one and not capped here, because the configuration count and the usable cores are not memory facts. `run_m1._table_build_threads` and the cycle's `artifact_cycle.kernel_threads_budget` apply those caps; this module cannot name `conform.SETTLEMENT_CONFIGS`, since `conform` imports it. A value that is not a bare count (a typo, a `GB` suffix, an empty variable) raises instead of falling back to the arithmetic. `AMS_TOTAL_MEMORY_BYTES` ignores such a value, because it only reproduces another machine. This variable is what someone sets to keep a build out of swap, and a width they did not ask for defeats that. `total_bytes` is a keyword so a test can compute the width for an invented machine. The alternative, `importlib.reload`, re-runs module scope, resets `_BUILT` and deletes the live `_SPEC_DUMPS` directories under a caller still holding a `spec_path`.

    `coresident_bytes` is for a caller that runs the fan-out beside something else: the artifact cycle, whose pytest pool runs for the whole table build. Those bytes are subtracted along with `default`'s memo before the division, so the width fits the machine the fan-out will run on. `AMS_KERNEL_THREADS` still overrides it, because nothing derived here may narrow a stated width. A bare `run_m1` has nothing beside it and passes `0`. Callers use this function instead of calling `how_many_fit` themselves, which would duplicate the override branch.
    """
    stated = os.environ.get("AMS_KERNEL_THREADS")
    if stated is not None:
        try:
            return max(1, int(stated))
        except ValueError:
            raise RuntimeError(
                f"AMS_KERNEL_THREADS={stated!r} is not a width: it takes a bare decimal count of configurations to hold in flight, and leaving it unset is what asks for the width this box's own memory derives."
            ) from None
    return memory_budget.how_many_fit(
        DELTA_PEAK_BYTES, coresident_bytes=DEFAULT_MEMO_BYTES + coresident_bytes, total_bytes=total_bytes
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
                f"AMS_REPLAY_THREADS={stated!r} is not a width: it takes a bare decimal count of settlement configurations to replay at once, and leaving it unset is what asks for the width this box's own memory derives."
            ) from None
    return memory_budget.how_many_fit(
        REPLAY_PEAK_BYTES, coresident_bytes=coresident_bytes, total_bytes=total_bytes
    )


# Computed once at import, so it is a plain module attribute that a parametrize list can read at import and a test can monkeypatch. `AMS_TOTAL_MEMORY_BYTES` therefore reaches it only from the environment the interpreter started in, not from a fixture. This is the solo width, with nothing co-resident but `default`'s memo, which is what a bare `run_m1` uses; the cycle computes its own through `artifact_cycle.kernel_threads_budget`.
KERNEL_THREADS_DEFAULT = kernel_threads_default()
TIMEOUT = 1800
# Every `cargo build` replaces the binary in target/release (it removes the file, then hard-links the new one in) even when nothing recompiled, so a build in one process can make another process's exec miss the file for an instant. This lock orders the two: a build holds it exclusively for the whole `cargo build`, and an invocation holds it shared for the spawn only, never for the run.
LOCK_PATH = MANIFEST.parent / "target" / ".ams-kernel-uplift.lock"
# How many lines of a failed build's stderr the exception includes: cargo reports the error in its last few lines, after the full compilation log.
BUILD_TAIL_LINES = 20
# On by default: the third join-count term is scored by the follower's simulated transition instead of seam-bearing candidacy. `AMS_SIMULATED_PROSPECT=0` turns it off for a comparison run, and run_m1's spawn-pool workers inherit that through the environment. It is read at call time, so a test may monkeypatch it; a caller that wants one named world regardless passes `SettlementModes`.
SIMULATED_PROSPECT_DEFAULT = os.environ.get("AMS_SIMULATED_PROSPECT", "1") != "0"
# On by default: follower votes are evaluated over the settled position's real shifted slots (vote right1 = position right2, right2 = position right3, right3 = position right4) instead of pinning every slot past the vote's own right1 to UNKNOWN, so a chained vote resolves inside the window instead of firing optimistically wherever its then: hop read the pin. `AMS_VOTE_SLOTS=0` is the comparison state. It is a module attribute read at call time, like SIMULATED_PROSPECT_DEFAULT.
VOTE_SLOTS_DEFAULT = os.environ.get("AMS_VOTE_SLOTS", "1") != "0"
# On by default: deep window slots are enumerated at class grain, one row per outcome fiber, expanded back to labels for every fold-side consumer. It is a kernel invocation flag passed by `world_flags` like the two defaults above, read at call time, with `AMS_DEEP_CLASSES=0` the label-grain comparison state. `class_grain` states the grain rule the crate applies.
DEEP_CLASSES_DEFAULT = os.environ.get("AMS_DEEP_CLASSES", "1") != "0"
# The semantics flags a fixpoint's shape depends on, each as (the kernel flag that turns it off, the module holding the default, the attribute name). The module is named instead of closed over so a later call reads a monkeypatched attribute. Only a flag that is off appears on the command line, so the shipping world invokes the subcommand with none.
SETTLEMENT_FLAGS = (
    ("--candidacy-prospect", sys.modules[__name__], "SIMULATED_PROSPECT_DEFAULT"),
    ("--vote-slots-off", sys.modules[__name__], "VOTE_SLOTS_DEFAULT"),
)
WORLD_FLAGS = (
    *SETTLEMENT_FLAGS,
    ("--deep-classes-off", sys.modules[__name__], "DEEP_CLASSES_DEFAULT"),
)
GUARD_TAIL_TOKENS = {
    token.kind: token for token in (settle.EDGE, settle.SPACE, settle.ZWNJ, settle.NAMER_DOT, settle.UNKNOWN)
}
FormationGuard = settle.FormationGuard
# How many windows one `settle-cases` invocation takes on the sequence path, where every answer is a whole decoded trace. A spawn with its spec load costs about nine milliseconds and a case about fifteen microseconds to settle and serialize, so at this size the spawn is under a quarter of a batch's kernel time, while the decoded traces, each under a kilobyte of text, stay bounded.
SETTLE_CASE_BATCH_SIZE = 2048
# The same bound for `settle_windows`, where an answer decodes to a `Settled` only. It is eight times the trace batch because a settled record costs a fraction of a trace's memory. At this size the spawn is under a twentieth of a batch's kernel time, so a larger batch gains nothing measurable (issue #153 measured it).
SETTLE_WINDOW_BATCH = 16384

_BUILT = False
# The marker on a memo file's head line (`rebuild/kernel-rs/src/memo.rs`'s `MEMO_FORMAT`); `read_memo_head` rejects a file with any other marker.
MEMO_FORMAT = "ams-m1-memo/1"
# The stamp for a window enumeration that is not kept. `build-tables` always writes the windows payload, whose head is where a caller reads the rules and cells back from, and always requires a stamp for that head. A caller with no fingerprint over the rune files has no stamp to give, so it passes this word and deletes the payload after reading it instead of packing it, and the word never reaches an artifact.
UNSTAMPED_WINDOWS = "unstamped"

# One scratch dump per live spec, keyed on identity. Each entry holds the spec itself so its id cannot be reused while the entry exists. A dump costs a few milliseconds and a couple of hundred kilobytes, which the settlement functions would otherwise pay on every invocation. The cap is small because the callers that matter hold one spec, and eviction deletes the directory.
_SPEC_DUMPS: OrderedDict[int, tuple[ResolvedSpec, tempfile.TemporaryDirectory, Path]] = OrderedDict()
_SPEC_DUMPS_CAP = 4
_SPEC_DUMPS_LOCK = threading.Lock()

# The same structure for the guard verdicts, which cost a crate invocation instead of a file write.
_GUARD_SWEEPS: OrderedDict[int, tuple[ResolvedSpec, FormationGuard]] = OrderedDict()
_GUARD_SWEEPS_CAP = 4
_GUARD_SWEEPS_LOCK = threading.Lock()


class KernelBuildError(RuntimeError):
    """`cargo` is absent or the crate did not build. Distinct from a run failure, which is a binary that exists and answered badly."""


class KernelRunError(RuntimeError):
    """The binary refused the invocation, exited nonzero, complained on a clean exit, or left a stream unwritten."""


def cargo_build() -> None:
    """Build the kernel in release mode, as `make kernel-build` does, holding the uplift lock exclusively. Checking that the binary exists is not enough, because a stale binary sits at the same path as a fresh one; building makes sure the sources on disk are what runs. A warm build takes a fraction of a second; `ensure_built` runs it once per process."""
    arguments = ["cargo", "build", "--release", "--manifest-path", str(MANIFEST)]
    try:
        with _uplift_lock(fcntl.LOCK_EX):
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


class _UpliftLock:
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


def _uplift_lock(mode: int) -> _UpliftLock:
    return _UpliftLock(mode)


def _run_kernel(arguments: list[str], verb: str) -> subprocess.CompletedProcess:
    """Spawn the binary with the uplift lock held shared for the spawn only, the moment a concurrent `cargo build` could make the path disappear, then wait without the lock so a long enumeration never blocks a build elsewhere. Raises `KernelRunError` for a missing binary or a timeout."""
    try:
        with _uplift_lock(fcntl.LOCK_SH):
            process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise KernelRunError(
            f"no kernel binary at {BINARY} — run `make kernel-build` first, or let the caller's cargo_build() build it"
        ) from None
    try:
        stdout, stderr = process.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise KernelRunError(
            f"the kernel gave no answer within {TIMEOUT} seconds on {verb} ({' '.join(arguments)})"
        ) from None
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def ensure_built() -> None:
    """Run `cargo_build` once per process and do nothing on later calls. A warm `cargo` still costs a fraction of a second, and a suite or cycle stage that builds a hundred tables would pay it a hundred times for a binary that cannot have changed. A caller that wants cargo consulted again calls `cargo_build` directly."""
    global _BUILT
    if _BUILT:
        return
    cargo_build()
    _BUILT = True


def world_flags() -> list[str]:
    """The mode flags that make the kernel enumerate this process's world: one per default that is off. The defaults are read at call time. `run_m1.tables_inputs` stamps the same three settings through `enumeration_tokens`, so an enumeration made with a flag on is never mistaken for one made with it off."""
    return [flag for flag, module, attribute in WORLD_FLAGS if not getattr(module, attribute)]


@dataclass(frozen=True)
class SettlementModes:
    """One named settlement world, for a caller that wants a world other than its process's. `current()` returns the process's own, read from the module defaults at call time. `flags()` returns the command-line flags for the two booleans, written and ordered by `SETTLEMENT_FLAGS`. Passing an explicit pair lets a caller, such as a test, choose a world without changing the module defaults every other caller in the process reads."""

    simulated_prospect: bool
    vote_slots: bool

    @classmethod
    def current(cls) -> SettlementModes:
        return cls(simulated_prospect=SIMULATED_PROSPECT_DEFAULT, vote_slots=VOTE_SLOTS_DEFAULT)

    def flags(self) -> list[str]:
        on = {
            "SIMULATED_PROSPECT_DEFAULT": self.simulated_prospect,
            "VOTE_SLOTS_DEFAULT": self.vote_slots,
        }
        return [flag for flag, _module, attribute in SETTLEMENT_FLAGS if not on[attribute]]


def settlement_flags(modes: SettlementModes | None = None) -> list[str]:
    """The two mode flags every direct settlement invocation takes, for `modes` or for this process's world. Deep-class grain applies only to enumeration, so it is not in this list."""
    if modes is None:
        modes = SettlementModes.current()
    return modes.flags()


def class_grain() -> bool:
    """Whether this process's enumeration splits deep slots into outcome fibers, restating the crate's grain rule for Python callers. `AMS_DEEP_CLASSES` asks for class grain, but fibers exist only where a deep token can change an outcome. In the pinned candidacy world, with neither the simulated prospect nor the shifted vote slots, nothing can, and the crate enumerates at label grain whatever the flag says. `enumeration_tokens` reads this, because the stamp on a serialized enumeration must distinguish the two grains."""
    return DEEP_CLASSES_DEFAULT and (SIMULATED_PROSPECT_DEFAULT or VOTE_SLOTS_DEFAULT)


def enumeration_tokens() -> list[str]:
    """The semantics tokens a stamp over this process's enumeration must include, in stamp order: the simulated prospect, the shifted vote slots and the class-grain deep slots, each present only while it is on. Each changes settlement semantics or enumeration grain without changing a hashed source, so a key over the sources alone would treat an enumeration made with a flag on as current in a process with it off, and the reverse. `run_m1.tables_inputs` appends these to the tables' stamp, `run_m1.memo_seed` joins them into the memo head's world, and `run_m1.locality_lines` includes them in the memo stamp, so the three agree."""
    tokens: list[str] = []
    if SIMULATED_PROSPECT_DEFAULT:
        tokens.append("simulated-prospect")
    if VOTE_SLOTS_DEFAULT:
        tokens.append("vote-slots")
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
        *world_flags(),
    ]
    if timings:
        arguments.append("--timings")
    finished = _run_kernel(arguments, "enumerate-configs")
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


def build_table_files(
    spec_path: Path,
    out_dir: Path,
    configs: Sequence[str],
    *,
    inputs: str,
    threads: int,
    timings: bool = False,
    timings_tag: str | None = None,
    config_seed: bool = True,
    seed: Path | None = None,
    edited: Sequence[str] = (),
    moved_classes: Sequence[str] = (),
    memo_stamp: str | None = None,
) -> dict[str, str]:
    """Every named configuration folded in the crate: its settlement TSV, its treaty TSV, its plain window enumeration stamped `inputs`, and the contract digest of the pair, returned as `{config: digest}`.

    This is `enumerate-configs` plus the fold in one process, so there is no stream between them: the fold runs on the product the worklist still holds. The windows payload is written uncompressed because the crate depends only on serde_json, and `run_m1.build_tables` packs it into the `.gz` artifact.

    One process handles every named configuration because the configurations after `default` are enumerated as deltas over it. `default` enumerates first and keeps its trace memo. Each other configuration reads that memo for every window whose key names none of its own unlocking runes and whose settlement read none of them, and settles only the rest, `threads` worker slots at a time, with `default`'s fold in one of them. `rebuild/kernel-rs/src/memo.rs` gives the argument; the configuration corollary of the window-locality theorem makes the shared results exact. `config_seed=False` enumerates every configuration from scratch, and `rebuild/test_kernel_exec.py` checks that it writes the same bytes as the delta build.

    The memo is also carried across builds. `seed` is a directory of a previous build's plain `memo-<config>.tsv` files. They are read with `edited` (the runes whose content changed since) and `moved_classes` (the predicate classes whose membership changed) excluded, so a window whose settlement read none of them settles as it did then. The crate's read journal records what each entry read (`rebuild/kernel-rs/src/index.rs`). `memo_stamp` is the stamp this build writes its own plain `memo-<config>.tsv` files under, beside the tables; `run_m1.build_tables` packs them into `.gz` artifacts. Python decides which files may be read and which runes count as edited (`run_m1.memo_seed`), from the stamp in each head; the crate checks only that a file matches its configuration and world.

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
        *world_flags(),
    ]
    if not config_seed:
        arguments.append("--config-seed-off")
    if seed is not None:
        arguments.append(f"--seed={seed}")
        if edited:
            arguments.append(f"--edited={','.join(edited)}")
        if moved_classes:
            arguments.append(f"--moved-classes={','.join(moved_classes)}")
    if memo_stamp is not None:
        arguments.append(f"--memo-stamp={memo_stamp}")
    if timings:
        arguments.append("--timings")
    finished = _run_kernel(arguments, "build-tables")
    errors = finished.stderr.decode(errors="replace").strip()
    if finished.returncode == 2:
        raise KernelRunError(
            f"kernel does not support build-tables yet, or rejected the invocation as a usage error: {errors} ({' '.join(arguments)})"
        )
    if finished.returncode != 0:
        raise KernelRunError(f"the kernel exited {finished.returncode} on build-tables: {errors}")
    _forward_stderr(errors, timings, arguments, timings_tag, verb="build-tables")
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
    return digests


def memo_path(out_dir: Path, config: str) -> Path:
    """Where one configuration's packed memo sits beside its tables."""
    return Path(out_dir) / f"memo-{config}.tsv.gz"


@dataclass(frozen=True)
class MemoHead:
    """What a memo file's head line records: the configuration and world it was traced in, which the crate checks the file against, and the opaque stamp its writer chose, which `run_m1.memo_seed` passes to `run_m1.memo_edited`."""

    config: str
    world: str
    stamp: str


def read_memo_head(path: Path) -> MemoHead | None:
    """The head of one packed memo, or None when there is no readable memo there — no file, another format, or a head short of its three fields."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            first = handle.readline()
    except OSError, EOFError, UnicodeDecodeError:
        return None
    marker, _tab, rest = first.rstrip("\n").partition("\t")
    if marker != f"# {MEMO_FORMAT}":
        return None
    fields = rest.split("\t", 2)
    if len(fields) != 3:
        return None
    return MemoHead(*fields)


class ReplayDisagreement(KernelRunError):
    """`replay-strings` found a window its rules and its engine settle differently, or one the engine refuses. The message is the crate's, naming the configuration, the window and the text it was reached in. A separate class lets a build tell this finding, a failed build naming a text, from a boundary failure."""


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
    horizon: int,
    families: Sequence[str] | None,
    threads: int,
    timings: bool = False,
    memo_dir: Path | None = None,
    memo_windows: int | None = None,
) -> dict[str, dict[str, int]]:
    """Replay every named configuration's persisted rules under `out_dir` over the string universe to `horizon`, and return `{config: {texts, windows, skipped}}` on a clean walk. With `families`, only the texts naming one of those runes are walked. Rules apply first-match with the settled left fed forward, and each window is checked against the crate's own settlement. `windows` counts window settles: each distinct window once on a walk with no ceiling, and again each time it is met after a release. The spec is the same memoized dump the settlement functions and the guard sweep read.

    A disagreement or a refused window raises `ReplayDisagreement` with the crate's message. Every other failure the CLI contract distinguishes raises `KernelRunError`, as does a clean exit whose output names a different set of configurations from the one requested. An empty `families` raises `ValueError` before anything is spawned, because the subcommand treats it as a usage error and a caller with nothing to walk has nothing to ask.

    With `memo_dir`, each passing walk writes its window memo to `replay_memo_dump(memo_dir, config)`: every distinct window it settled, keyed in the crate's own form, with every distinct settled record beside them. The result is unchanged. The build asks for one on a whole-universe walk, so the settle memo every later phase loads is filled here instead of by the oracle. The deep walk (`rebuild/tools/deep_replay.py`) never asks for a memo and passes `memo_windows` instead, which reaches the subcommand as `--memo-windows=`: the most windows a walk keeps memoized before it releases its memos and continues. Passing both raises `ValueError` before anything is spawned, because a walk that released its memo holds only the windows settled since, and a walk that writes its memo has no ceiling. A ceiling below one window also raises.
    """
    if families is not None and not families:
        raise ValueError("replay_strings takes a non-empty family list or None for the whole universe")
    if memo_windows is not None and memo_windows < 1:
        raise ValueError(f"replay_strings takes a memo ceiling of at least one window, not {memo_windows}")
    if memo_windows is not None and memo_dir is not None:
        raise ValueError(
            "replay_strings takes a memo directory or a memo ceiling, not both: a walk that files its memo walks with no ceiling, since a released memo holds only the windows settled since the release"
        )
    spec_path = _spec_dump(spec)
    ensure_built()
    arguments = [
        str(BINARY),
        "replay-strings",
        str(spec_path),
        str(out_dir),
        f"--configs={','.join(configs)}",
        f"--horizon={horizon}",
        f"--threads={threads}",
        *settlement_flags(),
    ]
    if families is not None:
        arguments.append(f"--families={','.join(families)}")
    if memo_dir is not None:
        arguments.append(f"--memo-dir={memo_dir}")
    if memo_windows is not None:
        arguments.append(f"--memo-windows={memo_windows}")
    if timings:
        arguments.append("--timings")
    finished = _run_kernel(arguments, "replay-strings")
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
    return answered


class EmittedOrderDisagreement(KernelRunError):
    """`replay-emitted` found a row the shipped settlement order settles differently from its configuration's table. The message is the crate's, naming the configuration, the row, the emitted rule that fired and the table's own rule. A separate class lets a build tell this finding from a boundary failure."""


def replay_emitted(
    windows: Path,
    *,
    config: str,
    table: Path,
    order: Path,
    context: Path,
    timings: bool = False,
) -> dict[str, int]:
    """Walk one configuration's packed window enumeration against the shipped settlement order with the crate's `replay-emitted` subcommand (`rebuild/kernel-rs/src/shipped_order.rs`). Returns `{rows, expanded}` on a clean walk, where `expanded` counts the rows tried member by member because an emitted lookahead class contained their deep class only in part. `windows` is the `.gz` the build packed (`table.windows_path`). It is decompressed here and streamed to the subcommand's standard input, because the crate reads the plain payload and has no decompressor. `table` is the configuration's settlement TSV, `order` the file `emit_gsub.emitted_order_tsv` writes, and `context` the one `emit_gsub.emitted_context_tsv` writes. A disagreement raises `EmittedOrderDisagreement` with the crate's message; every other failure raises `KernelRunError`."""
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
        with _uplift_lock(fcntl.LOCK_SH):
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
        # The subcommand stops reading after its disagreement limit, so a pipe closed under the writer means the walk has already finished, not a boundary failure. The sink is opened first so that a payload that cannot be opened still closes the subcommand's standard input.
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
        or set(answer) != {"config", "rows", "expanded"}
        or answer["config"] != config
    ):
        raise KernelRunError(
            f"replay-emitted answered {answer!r} where a {{config: {config!r}, rows, expanded}} line was asked for"
        )
    return {key: int(answer[key]) for key in ("rows", "expanded")}


def _forward_stderr(
    errors: str,
    timings: bool,
    arguments: list[str],
    tag: str | None = None,
    verb: str = "enumerate-configs",
) -> None:
    """Copy the kernel's timing lines to this process's stderr and raise on anything else. Of the flags this module passes, `--timings` is the only one that writes to stderr on a clean exit, and it writes only `[t] <label> <secs>s` lines, buffered and written in `--configs` order. Copying them verbatim puts the kernel's per-configuration wall-clock times in the same journal as the Python stage's, since `cycle_timings` reads both from a step's captured output. A `tag` in brackets is appended to each label that does not already end in one, so a fan-out that runs one process per configuration stays attributable."""
    if not errors:
        return
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
    for line in lines:
        sys.stderr.write((_tagged(line, tag) if tag else line) + "\n")
    sys.stderr.flush()


def _tagged(line: str, tag: str) -> str:
    marker, _, rest = line.partition(" ")
    label, separator, tail = rest.partition(" ")
    if not separator or label.endswith("]"):
        return line
    return f"{marker} {label}[{tag}] {tail}"


def _json_result(text: str):
    """Parse one answer's text as JSON. This is the trace shape and the default decode for `_settle_cases`, so a caller without its own `decode` gets the parsed result."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise KernelRunError(f"the answer is not JSON: {error.msg}") from None


def _settle_cases(
    spec_path: Path,
    cases_path: Path,
    cases: Sequence[str],
    features: frozenset[str],
    modes: SettlementModes | None = None,
    decode=_json_result,
    settled_only: bool = False,
):
    """Write the case file, run `settle-cases` over it and the already-dumped spec, and check that the kernel returned one answer per question without changing or reordering any. The check is on bytes: the crate echoes each question line verbatim before its answer, so an answer line must start with its own question and a tab, and the question is never parsed back. A question the crate cannot read makes it exit with an error instead of answering. `decode` reads one answer's text (everything after that tab) into whatever the caller keeps, the parsed JSON by default. It runs once per distinct answer text in the batch, since identical answers decode to the same value. That keeps a batch's Python cost proportional to the distinct answers: the review surface's windows overlap heavily, so much of every batch repeats a trace already decoded. `settled_only` asks for the settled record's seven fields instead of the trace, which `settle_windows` reads through `_settled_of_fields`."""
    cases_path.write_text("".join(line + "\n" for line in cases), encoding="utf-8")
    arguments = [str(BINARY), "settle-cases", str(spec_path), str(cases_path)]
    if features:
        arguments.append(f"--features={','.join(sorted(features))}")
    if settled_only:
        arguments.append("--settled-only")
    arguments.extend(settlement_flags(modes))
    finished = _run_kernel(arguments, "settle-cases")
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
        raise KernelRunError(f"settle-cases returned {len(lines)} answers for {len(cases)} questions")
    answers = []
    decoded: dict[str, object] = {}
    for line_number, (line, question) in enumerate(zip(lines, cases), 1):
        cut = len(question)
        if not line.startswith(question) or len(line) <= cut or line[cut] != "\t":
            raise KernelRunError(f"settle-cases line {line_number} changed or reordered its question")
        text = line[cut + 1 :]
        try:
            value = decoded[text]
        except KeyError:
            try:
                value = decoded[text] = decode(text)
            except KernelRunError as error:
                raise KernelRunError(f"settle-cases line {line_number}: {error}") from None
        answers.append(value)
    return answers


def _settle_batch(
    spec: ResolvedSpec,
    cases: Sequence[str],
    features: frozenset[str],
    modes: SettlementModes | None,
    decode,
    settled_only: bool = False,
) -> list:
    """Settle one batch in one invocation. The spec dump is the memoized one, so only the case file is written per call, in a scratch directory removed on return."""
    if not cases:
        return []
    spec_path = _spec_dump(spec)
    with tempfile.TemporaryDirectory() as scratch:
        cases_path = Path(scratch) / "cases.tsv"
        ensure_built()
        return _settle_cases(spec_path, cases_path, cases, frozenset(features), modes, decode, settled_only)


def settle_cases(
    spec: ResolvedSpec,
    cases: Sequence[str],
    features: frozenset[str],
    modes: SettlementModes | None = None,
    decode=None,
) -> list:
    """Settle a batch of windows through the crate in one invocation. The cases are question lines (`case_line` builds one), and each answer is the crate's whole trace. Without `decode`, the list holds each window's parsed result, which `trace_of` reads into a `TransitionTrace`. With `decode`, it holds what `decode` returned for each parsed result (`settle_sequences` wants a trace or a refusal), decoded once per distinct result in the batch (`_settle_cases`), so a caller that wants one model value per window never builds a list of answer dictionaries. A caller that wants only the settled record should use `settle_windows`, which asks the crate for that record instead of a trace. `modes` names the settlement world; without it the process's defaults apply."""
    if decode is None:
        return _settle_batch(spec, cases, features, modes, _json_result)
    return _settle_batch(spec, cases, features, modes, lambda text: decode(_json_result(text)))


# The index of the rune being settled among a question line's tab-separated fields: the left's kind and its seven record fields come first, the four right slots after.
CASE_INPUT_FIELD = 8
_NO_RECORD = ("",) * 7


def case_line(left: settle.LeftContext, token: settle.RightToken, rights: Sequence[settle.RightToken]) -> str:
    """One independent window as a `settle-cases` question line, tab-separated: the left's kind and its record (rune, stance, entry, exit, comma-joined adjustments, seam, extension; all seven empty for a left with no record, and a height or seam empty where there is none), then the rune being settled and the four raw slots after it, each a rune name or the kind name of a boundary or unknown slot. The crate echoes the line verbatim before its answer, which is how a batch's answers are matched to its questions."""
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
            settled.seam or "",
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
    """Raise the crate's refusal answer as `settle.SettleError`. A well-formed refusal is `{raise, message}` and nothing else: the raise identity becomes the error's bucket and the crate's message becomes the error message verbatim, so nothing downstream has to parse text to tell refusals apart. Anything else with a `raise` key means the boundary is wrong, not the window, and raises `KernelRunError`."""
    if "raise" not in result:
        return
    bucket = result.get("raise")
    message = result.get("message")
    if set(result) != {"raise", "message"} or not isinstance(bucket, str) or not isinstance(message, str):
        raise KernelRunError(f"settle-cases returned a malformed refusal: {result!r}")
    raise settle.SettleError(message, bucket)


# The pieces of a trace repeat far more than traces do: a batch of tens of thousands of answers names a few hundred distinct candidates, rungs, eliminations and settled cells, and building a frozen dataclass costs more than looking one up. So each piece is built once per distinct row and shared, keyed on the row's values; sharing is invisible to a reader because every piece is immutable. A table that reaches `_INTERN_CAP` entries is cleared, which bounds each kind at that many objects in a long process.
_INTERN_CAP = 8192
_CANDIDATES: dict[tuple, settle.Candidate] = {}
_RUNGS: dict[tuple, settle.RankedCandidate] = {}
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


def _rung_of(row) -> settle.RankedCandidate:
    if not isinstance(row, list) or len(row) != 3 or not isinstance(row[0], list):
        raise KernelRunError("settle-cases returned a malformed ranked ladder")
    try:
        return _interned(
            _RUNGS,
            (tuple(row[0]), row[1], row[2]),
            lambda: settle.RankedCandidate(_candidate_of(row[0]), row[1], row[2]),
        )
    except TypeError:
        raise KernelRunError("settle-cases returned a malformed ranked ladder") from None


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
    """Decode one settled record in the crate's JSON form (`types::settled_json`), `{"cell": [rune, stance, entry, exit, [adjustments]], "seam": …, "extension": …}`, to an interned `Settled`. A `settle-cases` trace carries this shape under `settled`, and the replay's window memo carries one per line, so both decode here. The interning key is `(rune, stance, entry, exit, adjustments, seam, extension)` with each absent height `None`. `_settled_of_fields` builds the same key from the tab-separated form, so a record read from either answer shape is the same object."""
    if not isinstance(row, Mapping) or set(row) != {"cell", "seam", "extension"}:
        raise KernelRunError(f"the kernel spelled a malformed settled record: {row!r}")
    cell_row = row["cell"]
    if not isinstance(cell_row, list) or len(cell_row) != 5 or not isinstance(cell_row[4], list):
        raise KernelRunError(f"the kernel spelled a malformed cell: {cell_row!r}")
    try:
        return _interned(
            _SETTLED,
            (*cell_row[:4], tuple(cell_row[4]), row["seam"], row["extension"]),
            lambda: Settled(
                CellId(cell_row[0], cell_row[1], cell_row[2], cell_row[3], tuple(cell_row[4])),
                row["seam"],
                row["extension"],
            ),
        )
    except TypeError:
        raise KernelRunError(f"the kernel spelled a malformed cell: {cell_row!r}") from None


def _settled_of(result) -> Settled:
    """The settled cell alone from a trace, for a caller that does not need the ladder that chose it."""
    if not isinstance(result, Mapping):
        raise KernelRunError(f"settle-cases returned a malformed result: {result!r}")
    _refusal(result)
    return settled_of_row(result.get("settled"))


# The number of fields in a settled-only answer, in `types::settled_fields` order: the same seven a question uses for its left record.
_SETTLED_FIELDS = 7


def _settled_of_fields(text: str) -> Settled:
    """Decode one settled-only answer to an interned `Settled`. The answer is seven tab-separated fields (rune, stance, entry, exit, comma-joined adjustments, seam, extension, with a height empty where there is none), or, starting with `{`, the crate's refusal object, which `_refusal` raises as it does for a trace. The interning key is the one `settled_of_row` builds, so a record read from either answer shape is the same object."""
    if text.startswith("{"):
        result = _json_result(text)
        if not isinstance(result, Mapping):
            raise KernelRunError(f"settle-cases returned a malformed result: {result!r}")
        _refusal(result)
        raise KernelRunError(f"settle-cases answered a settled-only question with an object: {text!r}")
    fields = text.split("\t")
    if len(fields) != _SETTLED_FIELDS:
        raise KernelRunError(f"the kernel spelled a malformed settled record: {text!r}")
    rune, stance, entry, exit_height, adjustments, seam, extension = fields
    try:
        extension_value = int(extension)
    except ValueError:
        raise KernelRunError(f"the kernel spelled a malformed settled record: {text!r}") from None
    entry_height = entry or None
    exit_height = exit_height or None
    adjustment_tokens = tuple(adjustments.split(",")) if adjustments else ()
    seam_height = seam or None
    return _interned(
        _SETTLED,
        (rune, stance, entry_height, exit_height, adjustment_tokens, seam_height, extension_value),
        lambda: Settled(
            CellId(rune, stance, entry_height, exit_height, adjustment_tokens), seam_height, extension_value
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
    """Read one answer's `result` into the trace every author-facing consumer renders: the settled cell, the ranked ladder, the eliminations with their YAML provenance, the deciding stage, the runner-up and the notes."""
    if not isinstance(result, Mapping):
        raise KernelRunError(f"settle-cases returned a malformed result: {result!r}")
    _refusal(result)
    expected = {
        "settled",
        "prospect",
        "joint_floor",
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
    ranked = tuple(map(_rung_of, result["ranked"]))
    eliminations = tuple(map(_elimination_of, result["eliminations"]))
    runner_up = None if result["runner_up"] is None else _candidate_of(result["runner_up"])
    return settle.TransitionTrace(
        settled=_settled_of(result),
        joint_floor=result["joint_floor"],
        prospect=result["prospect"],
        ranked=ranked,
        eliminations=eliminations,
        decided_stage=result["decided_stage"],
        runner_up=runner_up,
        notes=tuple(result["notes"]),
    )


def _tolerated_settled_fields(text: str) -> Settled | None:
    """`_settled_of_fields`, returning `None` for a refusal instead of raising. Only the crate's own refusal is caught: a malformed answer still raises `KernelRunError`, because it means the boundary is wrong, not the window."""
    try:
        return _settled_of_fields(text)
    except settle.SettleError:
        return None


def _trace_or_refusal(result) -> settle.TransitionTrace | settle.SettleError:
    """`trace_of`, returning a refusal instead of raising it. A batch decodes each distinct result once, so the refusal has to be carried back to the sequence that asked, which is the only place that knows whether to raise it or drop the sequence. A malformed answer still raises `KernelRunError`."""
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
) -> list[Settled | None]:
    """One `Settled` per case, in the order the cases were given, decoded directly from each answer line. The conform walker uses this: it keeps only each window's outcome, so it asks the crate for the settled record alone (`--settled-only`, seven tab-separated fields) instead of a trace whose ladder nothing reads. `batch` limits how many windows one invocation takes.

    `on_error="raise"` raises a refusal from the batch that met it, with the crate's message naming the left and the input. `on_error="drop"` puts `None` in that case's slot and decodes every other line as usual. A caller that settles windows it did not choose (the witness stage, which prefills every candidate string it might read) wants the other results, and wants a refusal to surface only where something reads that window. A malformed answer means the boundary is wrong, not the window, and raises `KernelRunError` in either mode.
    """
    decode = _tolerated_settled_fields if on_error == "drop" else _settled_of_fields
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

    The caller does tokenization and formation, since a caller that already has its tokens should not have to pass codepoints to re-derive them; `settle_codepoints` does both. `on_error="raise"` raises a refusal at the wave that met it, with the crate's message naming the left and the input. `on_error="drop"` returns `None` for that one sequence and lets every other sequence in the wave finish. A caller sweeping texts it does not control (the table diff's `WitnessIndex`) wants the other results, not the first error.
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
                answers = settle_cases(
                    spec, [case for _state, case in chunk], features, modes=modes, decode=_trace_or_refusal
                )
                for (state, _case), trace in zip(chunk, answers):
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
    """Run `guard-sweep` over one already-dumped spec and parse its complete output. Without `config` the verdicts quantify over the powerset; with one they are that configuration's, named as `conform.ACCEPTANCE_CONFIGS` names it so the no-feature configuration can be named. Completeness and uniqueness are checked here instead of left to a consumer's lookup miss, because a clean kernel exit that omitted or duplicated a row is a boundary failure, not an emitter error."""
    arguments = [str(BINARY), "guard-sweep", str(spec_path)]
    if config is not None:
        arguments.append(f"--config={config}")
    finished = _run_kernel(arguments, "guard-sweep")
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
        raise KernelRunError(f"guard-sweep returned an incomplete surface ({', '.join(detail)} verdicts)")
    return verdicts


def guard_sweep(spec: ResolvedSpec) -> FormationGuard:
    """The crate's complete configuration-independent section 5.7 guard verdicts for `spec`, parsed into Python model tokens. One `guard-sweep` invocation runs per spec identity per process, however many callers ask. The sweep takes about a fifth of a second and has a few thousand entries, and formation comes before every other stage, so a surface build, an emitter and a walker in one process would otherwise each spawn for the same result. The returned mapping is the memo's own and is shared; treat it as read-only and copy it before changing it."""
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
    """One configuration's reachable windows, enumerated by the crate and parsed into a `table.FixpointProduct`. The product holds everything the fold reads and nothing else the engine touched, so a consumer that wants what a table drops (the settled cells, the seams, the optimistic prospect, the fired provenance per row) asks for the product. Only tests call this; `build_tables` folds in the crate without writing a stream. Nothing survives the call: the spec dump and the stream live in a scratch directory removed on return."""
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        spec_path = directory / "spec.json"
        kernel_io.write_spec(spec, spec_path)
        ensure_built()
        streams = enumerate_configs(
            spec_path, directory / "streams", [feature_config_token(features)], threads=1
        )
        return read_stream(next(iter(streams.values())))


def build_tables(spec: ResolvedSpec, features: frozenset[str]) -> tuple[DecisionTable, TreatyTable]:
    """One configuration's decision and treaty tables, in memory, leaving no files: one `build-tables` process over a scratch spec dump, then the windows payload and the treaty TSV read back. The rows are read in full here, unlike on the build's own path, because a caller of this function wants the table, and the table is fixture-sized; `run_m1.build_tables` builds the live alphabet's. The crate raises its own errors during enumeration and folding (E-STRANDED, and the fold's checks listed in `rebuild/kernel-rs/src/fold.rs`), so a returned table has passed them."""
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        spec_path = directory / "spec.json"
        kernel_io.write_spec(spec, spec_path)
        ensure_built()
        config = feature_config_token(features)
        tables = directory / "tables"
        build_table_files(spec_path, tables, [config], inputs=UNSTAMPED_WINDOWS, threads=1)
        with (tables / f"windows-{config}.tsv").open("rt", encoding="utf-8") as handle:
            _stamp, decision = table.read_windows(handle)
        return decision, table.read_treaty_tsv(tables / f"treaties-{config}.tsv")
