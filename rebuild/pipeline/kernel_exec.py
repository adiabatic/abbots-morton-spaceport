"""The kernel boundary's Python face (issue #40, sub-issue #47; the only fixpoint there is since issue #78, and the only way this side settles anything at all): build the binary, fan one process out over a whole cycle's transition streams, read the section 5.7 guard surface, settle batched windows for explain, review, conform and the tests, and hand back the single-configuration product and tables for everything that is not `run_m1`. It lives here rather than beside the `rebuild/tools/kernel_*.py` harnesses because the pipeline is what calls it, and what the pipeline takes from the tools tree it takes one stdlib-only yardstick at a time — `peak_rss` for what a thing costs, `memory_budget` for how many of them fit — never a harness.

The semantics defaults live here too, beside the flags that carry them across the boundary: `SIMULATED_PROSPECT_DEFAULT` and `VOTE_SLOTS_DEFAULT` belong to settlement, `DEEP_CLASSES_DEFAULT` to enumeration, and each is a module attribute consulted at call time, so the environment is the only lever a whole process has and a test can monkeypatch one. A caller that wants a named world instead of this process's builds a `SettlementModes` and hands it to `settle_cases`, `settle_windows` or `settle_sequences`; nothing else puts a settlement flag on an argv.

The build is `cargo build --release` against the crate's own manifest and nothing else, because release is the only profile anything in this repo runs: the pipeline and the spec-echo parity test in `rebuild/test_kernel_io.py` both reach for `target/release/ams-m1-kernel`, and a debug binary that answered would answer far too slowly to be the same instrument. A box with no `cargo` is a `KernelBuildError` carrying the remedy rather than a stack trace, since that is the one failure a reader can fix in a minute; `ensure_built` is the memoized form every caller in a process shares, so a suite that builds a hundred tables pays for one build.

`build_table_files` is the verb `run_m1` needs: one process enumerates every settlement configuration and folds each in place, writing its settlement TSV, its treaty TSV and its plain window enumeration, and answering with the contract digest of each pair. There is no stream on that path at all — the fold reads the product the worklist still holds — so the several hundred megabytes a configuration's transitions would cost to write, read and hold parsed are never spent. The configurations past `default` are deltas over it: `default` enumerates first and keeps its trace memo, each other configuration reads that memo for every window naming none of its own unlocking runes and traces only the rest, and the same memo crosses builds through the `memo-<config>.tsv.gz` files packed beside the tables, read behind the runes whose content and the predicate classes whose membership moved, each memoized window refused by what its settlement read (`rebuild/kernel-rs/src/memo.rs` carries the argument, `run_m1.memo_seed` the decision). `enumerate_configs` is the stream fan-out beside it, which nothing on a build's path and no tool asks for: one process answers every named configuration, writing each one's stream to a file of its own, and the streams are byte-identical to what the same binary emits one configuration at a time at any thread width (sub-issue #46's exit bar). Threads are the caller's to choose because the ceiling is memory rather than CPU — a live configuration holds its whole working set until it has emitted — so `DELTA_PEAK_BYTES` is what one of them costs and `KERNEL_THREADS_DEFAULT` is `memory_budget.how_many_fit` over it: the box's own total, less the reserve that policy states and less `DEFAULT_MEMO_BYTES` — the memo `default` keeps alive for the wave, a snapshot rather than a live configuration — divided by a delta and floored at one, resolved once at import rather than asked again per build. A roomier box gets the width it actually has room for and a container gets the width its cgroup allows, neither of them by editing a constant. `AMS_KERNEL_THREADS` short-circuits ahead of the arithmetic in either direction, and since the artifacts are byte-identical at any width that override is purely a memory knob. Callers cap whatever width they are handed at the number of settlement configurations there are to answer (`conform.SETTLEMENT_CONFIGS`; the overlay configuration is never enumerated) and at the CPUs there are to answer them with. The string replay that follows a build has a divisor of its own, `REPLAY_PEAK_BYTES`, and `replay_threads_default` is the same division over it with nothing co-resident taken off first, since the replay's engines are built after the build's process has exited and no memo outlives it; `AMS_REPLAY_THREADS` is its short-circuit.

`build_tables` and `enumerate_transitions` are the single-configuration forms in memory, each writing nothing that outlives the call: one spec dumped to a scratch directory, and then either the two tables — `build-tables` into that directory, read back through `table.read_windows` and `table.read_treaty_tsv` — or the raw product, enumerated as a stream and parsed into a `table.FixpointProduct`. The first is how a test, a tool or a hand-assembled spec reaches a table; the second is the raw product a fold consumes, which no build stage and no tool asks for any more, and `rebuild/test_kernel_exec.py` is what keeps that path exercised.

`guard_sweep` is one other in-memory form: one crate invocation and one complete mapping from `(ligature, first raw slot, second raw slot)` to the config-blind formation verdict, memoized per spec identity so a process sweeps one spec once however many callers ask. `guard_sweep_under` is the same surface answered by one named configuration instead of the powerset — unmemoized, because nothing that ships reads it; it exists for the rebuild suite's pin of where each configuration's own surface stands against the quantified one. The settlement verbs sit beside it and share its spec dump, and so does `replay_strings`, the enumeration-completeness check `run_m1.run_replay_strings` runs after every table build: one `replay-strings` invocation over the settlement TSVs a build left, every text of the string universe — or only the texts naming the families the caller knows moved — walked in the crate against the crate's own settlement, and a disagreement is a `ReplayDisagreement` carrying the crate's sentence naming the configuration, the window and the text. `settle_cases` is the raw form — a file of independent windows in, one tab-separated question line each (`case_line` spells one), the full Rust trace objects out, with count and question echo checked, by the bytes, before anything decodes, and each distinct result decoded once for however many windows answered it. `settle_windows` asks the crate for the settled record alone — seven tab-separated fields under `--settled-only`, rather than a trace — and decodes each straight to a `Settled`, for the conform walker, which wants outcomes by the tens of thousands rather than traces; like `settle_sequences` it takes an `on_error`, so a caller prefilling windows it may never read can take `None` for a refusal and leave the rest of the batch standing. `settle_sequences` is what explain, the probe and the review surface reach for: the verb takes independent windows, while a sequence's next left context is the previous window's answer, so a batch of whole sequences advances in waves — all first positions, then all second positions off the first wave's answers — with boundary positions answered locally because they are model constants. `settle_codepoints` is the one-line form over a text. The CLI spells boundary tokens as `edge`, `space`, `zwnj`, `namer-dot`, and `unknown`; the guard mapping converts them to Python's `RightToken` constants at the boundary so consumers never confuse those model tokens with glyph names such as `uni200C` or `periodcentered`.

The codecs between the transport lines and the pipeline's model types live here as well — `case_line` on the way out, `trace_of` and `_settled_of_fields` on the way back — because every settlement caller needs them and none of them should be reaching into another consumer's module for one. A window the crate refuses answers `{raise, message}`, and that becomes a `settle.SettleError` carrying the crate's bucket and its sentence verbatim, so a caller can sort refusals without reading prose; an answer malformed in any other way is the boundary itself being wrong and stays a `KernelRunError`.

The invocation is read strictly, on the CLI contract's own terms: exit 2 is the usage check, which for a well-formed invocation can only mean the verb is absent or the two sides' flag sets have drifted apart; any other nonzero exit is the kernel complaining about its inputs; and stderr on a clean exit is a failure unless timings were asked for, in which case every `[t]` line is forwarded to this process's own stderr verbatim so the cycle journal reads the kernel's per-configuration walls the same way it reads Python's, and anything else on that stream is still a failure. Enumeration answers in files, so bytes on stdout there are a failure; `build-tables` answers in files too but reports its digests on stdout, one JSON object per line, and the set is checked against the configurations that were asked for; `guard-sweep` answers on stdout and its complete TSV surface is parsed strictly.
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
# What one delta holds through enumeration and its memo write, the divisor bounding each delta in flight: a delta that shares nothing with default's memo traces the whole configuration, so it is bounded by a configuration enumerated from scratch, and the seed is the worst delta rather than default. With ·Zoo in the alphabet, one `build-tables` over every settlement configuration, four deltas wide with memo files written and no previous-build seed, peaks at 14.78 GB whole-process under `/usr/bin/time -l`, which is DEFAULT_MEMO_BYTES plus about 3.1 GB per delta in flight; each delta's `--cache-census` trace_cache length reports how much it traces beyond default's memo. The headroom past that reading covers the memo writer's sorted window list beside the enumeration and a wave whose deltas peak together, and it errs high on purpose: a per-unit cost that errs low is what puts a box into swap, while one that errs high only narrows the wave. The pair is chosen together with DEFAULT_MEMO_BYTES so that both fleet boxes (`doc/fleet.md`) seat the whole delta wave at once — every configuration past default in one round — solo and under a gated cycle with gate:make-test's pytest pool on the box; `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` pin that criterion. Re-measure the two together, never one alone, whenever the alphabet grows or a per-configuration working set changes: a memo term seeded at default's whole resident reading beside a divisor a little under this one lands the 32 GiB box back on a split wave. The recipe is the measurement above, run in both arms — from scratch, and seeded over a previous build's memo files, the arm every artifact-cycle pass after the first runs — since the seeded arm holds a second default snapshot for the wave (DEFAULT_MEMO_BYTES says which): one `build-tables` over every settlement configuration with memo output, `--cache-census`, and `/usr/bin/time -l`.
DELTA_PEAK_BYTES = 5_500_000_000
# What default's retained trace memo holds for the length of the delta wave, the co-resident term taken off the box before the division. default's memo write and fold run at one of the `--threads` seats (`run_configs_tables` in `rebuild/kernel-rs/src/fanout.rs`), so the product they hold is priced as a seat's — bounded by DELTA_PEAK_BYTES, since default enumerated from scratch is exactly what that divisor prices — and the wave is never wider than the width the division answered; on a box whose width exceeds the delta count (the 48 GiB box, where `run_m1._table_build_threads` caps the width at the configuration count) that seat is one the deltas would have left idle, so every delta and default's post phase are resident together there, one working set more than the deltas alone, still inside the seats the division priced. What is co-resident with every seat for the length of the wave is the `MemoSnapshot` alone — one hash map and its pools (`rebuild/kernel-rs/src/memo.rs`), kept at the release point in `rebuild/kernel-rs/src/fixpoint.rs` where every delta's memos go — a memo rather than a configuration held through enumeration, which is why it is smaller in kind than DELTA_PEAK_BYTES and priced separately. No census line reports it directly: `--cache-census`'s `resident_after_release` line for default reads 4.15 GB while the process still holds default's transitions and seats for the fold, so the snapshot is bounded from above by that reading and from the whole-process peak of a build holding every delta at once (14.78 GB) less what the deltas account for. On a seeded build the term stands for two snapshots, not one: `build_table_files` (`rebuild/kernel-rs/src/fanout.rs`) reads the previous build's default memo whole through `load_seed` and holds it beside the fresh snapshot for the length of the wave, so every artifact-cycle pass after the first carries a second default memo the from-scratch readings never see — the cycle's seeded pass four deltas wide records 15.89 GB against the 14.78 GB from-scratch arm. The pair is chosen with DELTA_PEAK_BYTES against the criterion its comment states, both fleet boxes seating the whole delta wave, and the two are re-measured together in both arms, never one alone. `TABLE_BUILD_PEAK_BYTES` is the whole-process reading the two bound.
DEFAULT_MEMO_BYTES = 2_500_000_000
# What the one `build-tables` process holds at its peak: default's retained memo beside every seat in flight — a delta held through its memo write, or default's own memo write and fold at the seat they take — bounded by DEFAULT_MEMO_BYTES plus DELTA_PEAK_BYTES times the width. The direct whole-alphabet measurement with ·Zoo, four deltas wide and writing memo files without a previous-build seed, peaks at 14.78 GB; the artifact cycle records a 17.09 GB build peak. Both readings are the 32 GiB box's, whose width of four seats three deltas beside default's post phase; the 48 GiB box seats all four deltas beside it, so its reading runs above these by default's post-enumeration working set and is the one to re-seed this figure from. This estimate keeps headroom above both readings because the configurations' peaks and memo writes need not coincide identically across runs. `make job-costs` watches the cycle's build peak against this figure; it does not divide a pool by it.
TABLE_BUILD_PEAK_BYTES = 20_000_000_000
# What one configuration's horizon-4 string replay holds at its peak, the divisor `run_m1.run_replay_strings`'s width comes from: the engine's trace memo over the windows the string universe's texts reach, with no liveness probe cascade beyond the prospect's own, plus the window memo's inverse label map and block buffer when the walk files its dump — a subset of what the enumeration's engine holds, which is why it is a fraction of DELTA_PEAK_BYTES and priced on its own rather than inherited from it. Nothing comes off the box before the division: the replay starts after the `build-tables` process has exited, so no configuration's retained memo is alive, and each worker loads one settlement TSV and builds its own engine. With ·Zoo in the alphabet, one `replay-strings` over `default` alone at `--threads=1` with `--memo-dir` on — the shipped path, since a whole-universe walk always files its dump — peaks at 2.50 GB under `/usr/bin/time -l` and walks 1,082,400 texts; the same walk over every settlement configuration at the configuration count peaks at 11.65 GB whole-process, 2.33 GB per worker, with no swaps and each configuration's `[t] replay[<config>]` line unchanged from the solo reading, which is what says the width was bought without paging. The headroom past the reading errs high on purpose, as DELTA_PEAK_BYTES does and for the same reason — a per-unit cost that errs low is what puts the box into swap, while one that errs high only narrows the wave — and it also stands for the neighbors no term subtracts: the replay shares run_m1's process tree with the glyph chain, the window packers and the shipped-order walkers, each a small, flat working set beside it (`run_m1._core_bound_threads` prices the last two). The criterion it is chosen against is that both fleet boxes (`doc/fleet.md`) seat every settlement configuration in one wave, solo and under a gated cycle with gate:make-test's pytest pool on the box; `rebuild/test_memory_budget.py` and `rebuild/test_artifact_cycle.py` pin it. Re-measure it the same way whenever the alphabet grows, `REPLAY_HORIZON` moves or the trace memo's entry changes shape: the two runs above, both with the memo dump on, and a per-configuration line that lengthens in the wide run is the width having been bought with paging.
REPLAY_PEAK_BYTES = 3_000_000_000


def kernel_threads_default(*, coresident_bytes: float = 0, total_bytes: int | None = None) -> int:
    """How many seats of the table build's wave — a delta configuration each, one of them taken by `default`'s memo write and fold — this box has room for at once beside `default`'s memo: `AMS_KERNEL_THREADS` wherever it is set at all, else `DELTA_PEAK_BYTES` divided into the box by `memory_budget.how_many_fit` with `DEFAULT_MEMO_BYTES` taken off it first — the memo `default` keeps alive for the wave, a snapshot of one hash map and its pools rather than a configuration held through enumeration, which is why the two terms are different numbers — after the reserve that policy states, floored at one. A stated width is floored at one and clamped no further, because the configuration count and the cores this process may actually run on are not memory facts and belong to `run_m1.build_tables`'s own `min()`. A value that is not a bare count — a typo, a `GB` suffix, a variable declared without one — raises rather than falling quietly through to the arithmetic, which is where this knob parts company with `AMS_TOTAL_MEMORY_BYTES`: that one reproduces a box and ignoring a typo in it costs a reproduction, while this one is what someone reaches for to keep a build out of swap, so handing them a width they did not ask for is the failure they set it to prevent. `total_bytes` is a keyword so an assertion about another box is a pure function over an invented one; the alternative is `importlib.reload`, which re-runs module scope, resets `_BUILT`, and drops the live `_SPEC_DUMPS` entries' temporary directories under a caller still holding a `spec_path`.

    `coresident_bytes` is for the caller that runs the fan-out beside something else rather than alone — the artifact cycle, whose pytest pool is hot from t=0 and stays hot across the whole table build — and it is what makes that sentence arithmetic instead of prose: the pool's bytes come off the box beside `default`'s memo before the division, so the width answers for the machine the fan-out will actually run on. It sits below `AMS_KERNEL_THREADS` deliberately, since a stated width is the operator's whole answer and nothing derived here may narrow it, and it is public rather than a second module attribute because what is co-resident is the caller's fact, not this module's — a bare `run_m1` has nothing beside it and takes the default `0`. This function is the whole of that arithmetic's home: a caller reaching `how_many_fit` itself would have to restate the override branch above, and a second copy of it is exactly the thing that can fall out of agreement with the knob's contract.
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
    """How many settlement configurations the string replay's one crate process walks at once, as far as memory decides it: `AMS_REPLAY_THREADS` wherever it is set at all, else `REPLAY_PEAK_BYTES` divided into the box by `memory_budget.how_many_fit` after the reserve that policy states, floored at one. The divisor differs in kind from `DELTA_PEAK_BYTES` and the division from `kernel_threads_default`'s: a replay loads one settlement TSV and builds its own engine after the `build-tables` process has exited, so no `default` memo is alive and nothing is taken off the box ahead of the division — one configuration's walk is the whole per-seat cost. What comes back is the memory answer alone; the configuration count and the cores this process may actually run on are not memory facts and belong to `run_m1._replay_threads`'s own `min()`, the one place that narrowing lives, for the reason `kernel_threads_default` leaves the same two to `run_m1.build_tables`: this module cannot name `conform.SETTLEMENT_CONFIGS`, since `conform` imports it. A stated width is floored at one and clamped no further here, and a value that is not a bare count raises rather than falling through to the arithmetic, exactly as `AMS_KERNEL_THREADS` is treated: the knob is what someone reaches for to keep the replay out of swap, and a width they did not ask for is the failure they set it to prevent.

    `coresident_bytes` is for the caller that runs the replay beside something else — the artifact cycle, whose pytest pool is hot across the whole run_m1 step — and `total_bytes` is a keyword so an assertion about another box is a pure function over an invented one. On both fleet boxes the configuration count binds ahead of the memory answer, with or without the pool subtracted, so the term is insurance for a smaller box rather than a number that moves the width here.
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


# Resolved once at import rather than asked again per build, which is what lets a parametrized test name it and what keeps the cycle's printed plan and the child's argv one number rather than two readings of the same box. The consequence is that `AMS_TOTAL_MEMORY_BYTES` reaches it only from the environment the interpreter started in, never from a fixture. This one is the solo width — nothing co-resident but `default`'s own memo — which is what a bare `run_m1` wants; the cycle names its own through `artifact_cycle.kernel_threads_budget` instead.
KERNEL_THREADS_DEFAULT = kernel_threads_default()
TIMEOUT = 1800
# Every `cargo build` re-uplifts the binary into target/release — removes it, then hard-links the fresh one in — even when nothing recompiled, so a build in one process can make another process's exec miss the file for an instant. One lock in the target directory orders the two: a build holds it exclusively for the uplift, an invocation holds it shared for exactly the spawn, and never for the run.
LOCK_PATH = MANIFEST.parent / "target" / ".ams-kernel-uplift.lock"
# How much of a failed build's stderr rides the exception: cargo says what is wrong in its last few lines and repeats the whole compilation above them.
BUILD_TAIL_LINES = 20
# The issue-28 flag, default on since stage 3: the third join-count term is scored by the follower's simulated transition instead of seam-bearing candidacy. Module-level so the default is one edit and a comparison run_m1 can opt out across its spawn-pool workers via AMS_SIMULATED_PROSPECT=0; consulted at call time, so a test may monkeypatch it and a caller wanting one named world regardless passes `SettlementModes`.
SIMULATED_PROSPECT_DEFAULT = os.environ.get("AMS_SIMULATED_PROSPECT", "1") != "0"
# The issue-28 stage-4b companion flag, default on: follower votes are evaluated over the seat's real shifted slots (vote right1 = seat right2, right2 = seat right3, right3 = seat right4) instead of pinning everything past the vote's own right1 to UNKNOWN, so a chained vote resolves inside the window instead of firing optimistically wherever its then: hop read the pin. Same plumbing contract as SIMULATED_PROSPECT_DEFAULT: module-level, consulted at call time, AMS_VOTE_SLOTS=0 is the comparison state.
VOTE_SLOTS_DEFAULT = os.environ.get("AMS_VOTE_SLOTS", "1") != "0"
# The issue-26 flag: deep window slots enumerate at class grain (one row per outcome fiber, expanded back to labels for every fold-side consumer). It is a kernel invocation flag, carried across by `world_flags` like the two semantics defaults above — module-level, consulted at call time, AMS_DEEP_CLASSES=0 the label-grain comparison state — and `class_grain` states the grain rule the crate itself applies.
DEEP_CLASSES_DEFAULT = os.environ.get("AMS_DEEP_CLASSES", "1") != "0"
# The three semantics flags a fixpoint's shape depends on, each as (the kernel flag that says it is off, the module holding the default, the attribute). All three defaults are this module's, and the module is named rather than closed over so a monkeypatched attribute is what a later call reads. Off is what carries a flag, so the shipping world invokes the verb bare.
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
# How many windows ride one `settle-cases` invocation on the sequence path, where every answer comes back as a whole decoded trace. A spawn with its spec load costs about nine milliseconds and a case about fifteen microseconds to settle and serialize, so at this size the spawn is a small fraction of a batch's kernel time while the decoded traces — under a kilobyte of text each — stay a bounded pile.
SETTLE_CASE_BATCH_SIZE = 2048
# The same bound for `settle_windows`, where an answer decodes to a `Settled` and nothing else. Eight times the trace batch, because the resident cost per case is a fraction of a trace's and the conform walker has more than a million distinct windows per configuration to get through. At this size the spawn is under a twentieth of a batch's kernel time, so a larger batch buys nothing measurable (issue #153 priced it).
SETTLE_WINDOW_BATCH = 16384

_BUILT = False
# The marker a memo file's head line carries (`rebuild/kernel-rs/src/memo.rs`'s `MEMO_FORMAT`); a file naming anything else is not read.
MEMO_FORMAT = "ams-m1-memo/1"
# The stamp a window enumeration nobody will keep is written under. `build-tables` always writes the payload — it is where the head a caller reads its rules and cells back out of comes from — and always demands a stamp for its head, but a caller with no fingerprint over the repo's rune files has none to give and deletes the payload unread rather than packing it, so the word it carried never reaches an artifact.
UNSTAMPED_WINDOWS = "unstamped"

# One scratch dump per live spec, keyed on identity and holding the spec strongly so an id can never be recycled underneath an entry. A dump costs a few milliseconds and a couple of hundred kilobytes, and the settlement verbs would otherwise pay for one per invocation; the cap is small because the callers that matter hold one spec, and eviction cleans the directory.
_SPEC_DUMPS: OrderedDict[int, tuple[ResolvedSpec, tempfile.TemporaryDirectory, Path]] = OrderedDict()
_SPEC_DUMPS_CAP = 4
_SPEC_DUMPS_LOCK = threading.Lock()

# The same shape for the guard surface, which is a whole crate invocation rather than a file write.
_GUARD_SWEEPS: OrderedDict[int, tuple[ResolvedSpec, FormationGuard]] = OrderedDict()
_GUARD_SWEEPS_CAP = 4
_GUARD_SWEEPS_LOCK = threading.Lock()


class KernelBuildError(RuntimeError):
    """`cargo` is absent or the crate did not build. Distinct from a run failure, which is a binary that exists and answered badly."""


class KernelRunError(RuntimeError):
    """The binary refused the invocation, exited nonzero, complained on a clean exit, or left a stream unwritten."""


def cargo_build() -> None:
    """Build the kernel in release mode, the way `make kernel-build` does. Callers run this before every fan-out rather than checking whether the binary exists: a stale binary and a fresh one are the same file, and what a caller needs is that the sources on disk are what answered. A warm build costs a fraction of a second; a cold one costs what a cold one costs."""
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
    """Invoke the binary with the uplift lock held shared across the spawn alone — the one instant a concurrent `cargo build` could make the path vanish — then wait unlocked, so a minutes-long enumeration never stalls a build elsewhere. Raises `KernelRunError` for a missing binary or a silent kernel, the same way for every verb."""
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
    """`cargo_build` once per process, and nothing at all on every call after. The build itself is what a caller wants before its first invocation — the sources on disk are what must answer — but a warm `cargo` still costs a fraction of a second, and a suite or a cycle stage that builds a hundred tables would pay it a hundred times over for a binary that cannot have moved underneath it. A caller that genuinely wants the toolchain consulted again calls `cargo_build` directly."""
    global _BUILT
    if _BUILT:
        return
    cargo_build()
    _BUILT = True


def world_flags() -> list[str]:
    """The mode flags the kernel needs to enumerate the world this Python process is in — one per default that is off. All three are this module's defaults, consulted at call time, so the environment is the only lever on the Python side and this is what carries it across to the kernel; the same three tokens ride `run_m1.tables_inputs`, so a flag-on enumeration can never be mistaken for a flag-off one on either side of the seam."""
    return [flag for flag, module, attribute in WORLD_FLAGS if not getattr(module, attribute)]


@dataclass(frozen=True)
class SettlementModes:
    """One named settlement world, for a caller that wants a world other than the one its process is in. `current()` is the process's own — the module defaults, read now rather than at import — and `flags()` is what those two booleans put on an argv, spelled and ordered by `SETTLEMENT_FLAGS` so there is one authority for the spellings. Passing an explicit pair is how a comparison, a pinned test, or a guard-grain caller asks for a world without touching the module defaults every other caller in the process is reading."""

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
    """The two mode flags shared by every direct settlement invocation, for `modes` or for this process's own world. Deep-class grain belongs only to enumeration, so it is deliberately absent from this narrower list."""
    if modes is None:
        modes = SettlementModes.current()
    return modes.flags()


def class_grain() -> bool:
    """Whether the enumeration this process asks for splits its deep slots into outcome fibers — the grain rule the crate applies, restated on the Python side for the callers that have to name it. `AMS_DEEP_CLASSES` asks for class grain, but the fibers have a source only where a deep token can move an outcome at all: in the pinned candidacy world, with neither the simulated prospect nor the shifted vote slots, there is nothing to probe and the crate enumerates at label grain whatever the flag says. `run_m1.tables_inputs` reads this, because the stamp on a serialized enumeration has to distinguish the two grains."""
    return DEEP_CLASSES_DEFAULT and (SIMULATED_PROSPECT_DEFAULT or VOTE_SLOTS_DEFAULT)


def enumeration_tokens() -> list[str]:
    """The semantics tokens any stamp over this process's enumeration has to carry, in the order a stamp appends them: the simulated prospect, the stage-4b shifted vote slots, and the issue-26 class-grain deep slots, each present only while it is on. Every one of them changes settlement semantics or enumeration grain without moving a hashed source, so a key over the sources alone would read a flag-on enumeration as fresh to a flag-off process and the reverse. `run_m1.tables_inputs` appends these to the tables' stamp and `artifact_cycle.conform_skip_lines` folds the same list into the sweep's key; deriving both from one function is what keeps the two from ever disagreeing about which engine produced what."""
    tokens: list[str] = []
    if SIMULATED_PROSPECT_DEFAULT:
        tokens.append("simulated-prospect")
    if VOTE_SLOTS_DEFAULT:
        tokens.append("vote-slots")
    if class_grain():
        tokens.append("deep-classes")
    return tokens


def _spec_dump(spec: ResolvedSpec) -> Path:
    """The path of this spec's `spec.json`, dumped once per spec identity per process. Every settlement verb and the guard sweep read the same file: the dump is the fixed cost of reaching the kernel at all, and a walker that settles a hundred batches of one spec should pay it once rather than a hundred times. The entry holds the spec strongly, so its `id` cannot be recycled while the dump stands for it, and holds the `TemporaryDirectory` so eviction is what removes the file rather than a garbage collection nobody scheduled."""
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
    """Every named configuration's transition stream, enumerated by one kernel process into `out_dir` and returned as `{config: path}`. The files are plain ndjson — the compression the artifacts wear is Python's job, since the crate carries serde_json and nothing else — and which file holds which configuration is the caller's own token, because the crate refuses a token that is not the canonical spelling of the features it names. Raises `KernelRunError` for every shape of refusal the CLI contract distinguishes, and for a run that exits clean having left a stream unwritten.

    `timings_tag` names the configuration a whole invocation stands for, and is what a caller running one process per configuration passes: the crate labels its per-configuration lines `enumerate[<config>]` already, but `spec_parse` and `enumerate_total` name the process rather than any configuration, and a fan-out of one process per acceptance configuration would leave an unattributable pair per configuration in the cycle journal. Tagged, they read `spec_parse[<config>]`.
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

    This is `enumerate-configs` plus the fold, in one process, and the reason there is no stream between them: the fold runs on the product the worklist still holds, so the several hundred megabytes a configuration's stream would cost to write, to read and to hold parsed are never spent. The windows payload lands uncompressed because the compressor stays on this side of the boundary — the crate carries serde_json and nothing else — and `run_m1.build_tables` is what packs it into the `.gz` the artifact is.

    One process answers every configuration named, because the configurations past `default` are enumerated as deltas over it: `default` enumerates first and keeps its trace memo, and each other configuration then reads that memo for every window naming none of its own unlocking runes and settles only the rest, `threads` seats at a time with `default`'s own memo write and fold at one of them (`rebuild/kernel-rs/src/memo.rs` carries the argument; the configuration corollary of the window-locality theorem is what makes the shared answers exact). `config_seed=False` is the from-scratch arm — every configuration enumerated on its own — which the contracts lane holds byte-identical to the delta.

    The same memo crosses builds. `seed` is a directory of a previous build's plain `memo-<config>.tsv` files, read behind `edited`, the runes whose content moved since, and `moved_classes`, the predicate classes whose membership did, so a window whose settlement read none of them settles as it settled then (the crate's read journal is what each entry is held to; `rebuild/kernel-rs/src/index.rs` carries it); `memo_stamp` is the stamp this build writes its own memo files under, plain `memo-<config>.tsv` beside the tables, which `run_m1.build_tables` packs into the `.gz` the artifact is. Which files may be read at all, and which runes count as edited, is decided on this side (`run_m1.memo_seed`) from the stamp the head carries; the crate holds a file to its configuration and world alone.

    The digests ride stdout as one JSON object per line, in the order the configurations were named, because a digest is a scalar its caller holds and reports rather than an artifact family of its own. Raises `KernelRunError` for every shape of refusal the CLI contract distinguishes, and for a clean exit whose answer does not name every configuration exactly once.
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
    """What a memo file's head line says: the configuration and the world it was traced in, which the crate holds a file to, and the opaque stamp its writer chose, which `run_m1.memo_edited` reads."""

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
    """The `replay-strings` verb found a window its rules and its engine answer differently, or one the engine refuses: the crate's own sentence, which names the configuration, the window and the text it was reached in. Its own class so a build can tell the finding from a boundary failure — the first is a red build naming a text, the second is the seam being wrong."""


# The head token of the window memo the `replay-strings` verb files per configuration under `--memo-dir` (`rebuild/kernel-rs/src/replay.rs`'s `MEMO_FORMAT`); `conform.absorb_replay_memo` refuses a file naming anything else.
REPLAY_MEMO_FORMAT = "ams-m1-replay-memo/1"


def replay_memo_dump(out_dir: Path, config: str) -> Path:
    """Where the `replay-strings` verb files one configuration's window memo under `out_dir` when asked for one (`fanout::replay_memo_path` spells the same name): a build input `run_m1.run_replay_strings` absorbs into the configuration's settle memo and deletes in the same phase, never an artifact."""
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
) -> dict[str, dict[str, int]]:
    """Every named configuration's persisted rules under `out_dir` replayed over the string universe to `horizon` — every text, or with `families` only the texts naming one of those runes — first-match with the settled left fed forward, each window held to the crate's own settlement; `{config: {texts, windows, skipped}}` on a clean walk. The spec rides the same memoized dump the settlement verbs and the guard sweep read. A disagreement or a refused window is a `ReplayDisagreement` carrying the crate's sentence; every other refusal the CLI contract distinguishes is a plain `KernelRunError`, as is a clean exit whose answer does not name every configuration exactly once. An empty `families` is refused here rather than handed across, because the verb reads it as a usage error and the caller that has nothing to walk has nothing to ask. With a `memo_dir`, each green walk files its window memo there at `replay_memo_dump(memo_dir, config)` — every distinct window it settled, keyed in the crate's own spelling, with every distinct settled record beside them — and the answer is unchanged; the build asks for one on a whole-universe walk so the settle memo every later phase loads is filled here rather than by the oracle, while the deep walk (`rebuild/tools/deep_replay.py`) never asks, since a horizon-5 memo is a multiple of the belt's."""
    if families is not None and not families:
        raise ValueError("replay_strings takes a non-empty family list or None for the whole universe")
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
    """The `replay-emitted` verb found a row the shipped settlement order answers differently from its configuration's table: the crate's own sentence, which names the configuration, the row, the emitted rule that fired and the table's own rule. Its own class so a build can tell the finding from a boundary failure."""


def replay_emitted(
    windows: Path,
    *,
    config: str,
    table: Path,
    order: Path,
    context: Path,
    timings: bool = False,
) -> dict[str, int]:
    """One configuration's packed window enumeration walked against the shipped settlement order by the crate's `replay-emitted` verb (`rebuild/kernel-rs/src/shipped_order.rs`): `{rows, expanded}` on a clean walk, `expanded` counting the rows tried member by member because an emitted look class admitted their deep class in part. `windows` is the `.gz` the build packed (`table.windows_path`), decompressed here and streamed to the verb's standard input, since the crate reads its own plain payload and carries no decompressor; `table` is the configuration's settlement TSV, `order` the file `emit_gsub.emitted_order_tsv` writes and `context` the one `emit_gsub.emitted_context_tsv` writes. A disagreement is an `EmittedOrderDisagreement` carrying the crate's sentence; every other refusal is a plain `KernelRunError`."""
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
        # The verb stops reading at its disagreement cap, so the pipe closing under the writer is a walk that has already answered, not a failure of the seam; the sink is opened first so that a payload that will not open still closes the verb's standard input.
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
    """Pass the kernel's timing lines through to this process's own stderr and refuse everything else. `--timings` is the one thing that writes to a clean exit's stderr, and it writes only `[t] <label> <secs>s` lines, buffered and flushed in `--configs` order; forwarding them verbatim is what puts the kernel's per-configuration walls in the same journal as the Python stage's, since `cycle_timings` reads both off a step's captured output. A `tag` bracket is appended to whichever labels do not carry one already, so a fan-out that spends one process per configuration stays attributable."""
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
    """One answer's text as the JSON value it spells: the trace shape, and the default `_settle_cases` decode, so a caller with no `decode` of its own gets the parsed result."""
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
    """Write the case file, invoke `settle-cases` over it and the already-dumped spec, and prove that the kernel returned one answer per question without changing or reordering any question — by the bytes: the crate echoes a question line verbatim ahead of its answer, so an answer line has to open with its own question and a tab, and the question is never parsed back. What proves the question was understood is the crate's refusals, which stop the run rather than answering. `decode` is the per-result seam: it reads one answer's text — everything after that tab — into whatever the caller keeps, the parsed JSON by default, and it runs once per distinct answer text in the batch, since a window that answered identically to another decodes to the same value. That is what keeps a batch's Python cost proportional to what the crate actually said rather than to how many windows said it: the review surface's windows overlap heavily, so a good fraction of every batch is verbatim repeats of a trace already decoded. `settled_only` asks for the settled record's seven fields instead of the trace, which `settle_windows` reads through `_settled_of_fields`."""
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
    """One batch through one invocation: the spec dump is the memoized one, so only the case file is written per call, and it goes with the frame."""
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
    """Replay a batch of settlement windows through the crate, one invocation over one batch. The cases are question lines — `case_line` builds one — and every answer is the crate's whole trace: without a `decode` the list holds each window's parsed result, which `trace_of` reads into a `TransitionTrace`; with one, it holds what `decode` made of each parsed result instead — `settle_sequences` wants a trace or a refusal — decoded once per distinct result in the batch (`_settle_cases`), so a caller that wants one model value per window never builds a list of answer dictionaries it would immediately throw away. `settle_windows` is the verb for a caller that wants the settled record and nothing else: it asks the crate for that record rather than decoding a trace down to one. `modes` names the settlement world; without one the process's own defaults answer."""
    if decode is None:
        return _settle_batch(spec, cases, features, modes, _json_result)
    return _settle_batch(spec, cases, features, modes, lambda text: decode(_json_result(text)))


# The seat of the rune under settlement among a question line's tab-separated fields: the left's kind and its seven record fields come first, the four right slots after.
CASE_INPUT_FIELD = 8
_NO_RECORD = ("",) * 7


def case_line(left: settle.LeftContext, token: settle.RightToken, rights: Sequence[settle.RightToken]) -> str:
    """One independent window as `settle-cases` reads it, one tab-separated line: the left's kind and its record — rune, stance, entry, exit, comma-joined adjustments, seam, extension, all seven empty for a left with no record and a height or seam empty where there is none — then the rune being settled and the four raw slots after it, each a rune name or the kind spelling of a boundary or unknown slot. The crate echoes the line verbatim ahead of its answer, which is what lines a batch's answers up with its questions."""
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
    """The crate's refusal answer, re-raised as this side's `SettleError`. A well-formed refusal is `{raise, message}` and nothing else: the raise identity becomes the error's bucket and the crate's sentence becomes the message verbatim, so nothing downstream has to parse prose to sort one refusal from another. Anything else wearing a `raise` key is the boundary being wrong rather than the window, and stays a `KernelRunError`."""
    if "raise" not in result:
        return
    bucket = result.get("raise")
    message = result.get("message")
    if set(result) != {"raise", "message"} or not isinstance(bucket, str) or not isinstance(message, str):
        raise KernelRunError(f"settle-cases returned a malformed refusal: {result!r}")
    raise settle.SettleError(message, bucket)


# The pieces a trace is made of repeat far more than traces do: a batch of tens of thousands of answers names a few hundred distinct candidates, rungs, eliminations and settled cells between them, and building a frozen dataclass costs more than reading one back. So each piece is built once per distinct row and shared, keyed on the row's own values — invisible to a reader, since every piece is immutable — and a table past its cap is cleared wholesale, which bounds a long process at a few thousand small objects per kind.
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
    """One `{"cell": [rune, stance, entry, exit, [adjustments]], "seam": …, "extension": …}` mapping as the crate spells a settled record in JSON (`types::settled_json`) decoded to an interned `Settled`: the shape a `settle-cases` trace carries under `settled` and the replay's window memo carries one record per line of, so both decode through this one codec. The interning key is `(rune, stance, entry, exit, adjustments, seam, extension)` with each absent height `None`, and `_settled_of_fields` builds the very same key from the tab-separated spelling, so a record read off either answer shape is one shared object."""
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
    """The settled cell alone off a trace, for a caller with no use for the ladder that chose it."""
    if not isinstance(result, Mapping):
        raise KernelRunError(f"settle-cases returned a malformed result: {result!r}")
    _refusal(result)
    return settled_of_row(result.get("settled"))


# The fields a settled-only answer spells, in `types::settled_fields`' order: the seven a question spells its left record in.
_SETTLED_FIELDS = 7


def _settled_of_fields(text: str) -> Settled:
    """One settled-only answer decoded to an interned `Settled`: seven tab-separated fields — rune, stance, entry, exit, comma-joined adjustments, seam, extension, a height empty where there is none — or, opening with `{`, the crate's refusal object, which `_refusal` raises exactly as it does off a trace. The interning key is the one `settled_of_row` builds, so a record read off either answer shape is one shared object."""
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
    """One answer's `result` read back into the trace every author-facing consumer renders: the settled cell, the ranked ladder, the eliminations with their YAML provenance, the stage that decided, and the notes."""
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
    """`_settled_of_fields` with a refusal answered as `None` rather than raised. Only the crate's own refusal is swallowed: a malformed answer raises `KernelRunError` out of here exactly as it would in the raising mode, because that is the boundary being wrong rather than the window."""
    try:
        return _settled_of_fields(text)
    except settle.SettleError:
        return None


def _trace_or_refusal(result) -> settle.TransitionTrace | settle.SettleError:
    """`trace_of` with a refusal handed back rather than raised, so a batch decoded once per distinct result can carry one window's refusal to the sequence that asked it — which is the only place that knows whether to raise it or drop the sequence. A malformed answer stays a `KernelRunError` out of here."""
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
    """One `Settled` per case, in the order the cases were asked, decoded straight off each answer line. This is the conform walker's verb: it settles distinct raw windows by the hundred thousand and keeps only the outcome, so it asks the crate for the settled record alone (`--settled-only`, seven tab-separated fields) rather than a trace that would be serialized, carried and decoded for a ladder nothing reads. `batch` bounds how many windows ride one invocation.

    `on_error="raise"` lets a refusal out of the batch that met it, which names the offending left and input in the crate's own sentence. `on_error="drop"` answers `None` in that one case's slot instead and decodes every other line as usual — a caller that settles windows it never chose to ask about (the witness gate, which prefills every candidate string it might read) wants the survivors, and wants a refusal to surface only where something actually reads that window. A malformed answer is the boundary being wrong rather than the window and stays a `KernelRunError` in either mode.
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
    """A trace per position for each already-formed token sequence, in the order the sequences were asked. This is the batching verb: `settle-cases` answers independent windows, while a sequence's next left context is the previous window's answer, so the batch advances in waves — every sequence's first position, then every sequence's second position off the first wave's answers — and each wave spends one invocation per feature configuration per `SETTLE_CASE_BATCH_SIZE` windows rather than one per sequence. Boundary positions never reach the kernel: they settle to a model constant and reset the left here.

    Tokenization and formation are the caller's, because a caller that already knows its tokens should not be handed a codepoint list to re-derive them from; `settle_codepoints` is the form that does both. `on_error="raise"` lets a refusal out at the wave that hit it, which names the offending left and input in the crate's own sentence. `on_error="drop"` instead answers `None` for that one sequence and leaves every other sequence in the wave to finish — a caller sweeping texts it does not control (the table diff's witness index) wants the survivors rather than the first complaint.
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
    """One text settled end to end: tokenize, form its ligatures against the guard surface, settle every position through the crate, and hand back the settled cells. `guard_verdicts` lets a caller with a sweep already in hand skip the memo lookup entirely."""
    if guard_verdicts is None:
        guard_verdicts = guard_sweep(spec)
    tokens = settle.form_ligatures(spec, settle.tokens_from_codepoints(spec, codepoints), guard_verdicts)
    traces = settle_sequences(spec, [(tokens, frozenset(features))])[0]
    assert traces is not None
    return [trace.settled for trace in traces]


def _guard_verdicts(spec: ResolvedSpec, spec_path: Path, config: str | None = None) -> FormationGuard:
    """Invoke `guard-sweep` over one already-dumped spec and parse its complete answer — quantified over the powerset when no `config` is named, and under that one configuration otherwise, spelled the way `conform.ACCEPTANCE_CONFIGS` spells it so the no-feature configuration is nameable. Completeness and uniqueness are checked here rather than left to a consumer's lookup miss, because a clean kernel exit that silently omitted or duplicated a row is a broken boundary, not an emitter error."""
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
    """The crate's complete config-blind section 5.7 verdict surface for `spec`, parsed into Python model tokens. Exactly one `guard-sweep` invocation runs per spec identity per process, however many callers ask: the sweep is a fifth of a second and a few thousand entries, and formation is staged before everything, so a surface build, an emitter and a walker in one process would otherwise each spawn for the same answer. The mapping handed back is the memo's own, shared and read-only by convention; a caller that needs to mutate one takes a copy."""
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
    """The section 5.7 verdict surface as one configuration alone answers it, in the same keys as `guard_sweep`'s. Every call is a crate invocation: nothing that ships reads a per-configuration surface, so there is no memo to keep, and the one caller — the rebuild suite's pin of where each configuration's own surface stands against the quantified one — asks once per configuration."""
    spec_path = _spec_dump(spec)
    ensure_built()
    return _guard_verdicts(spec, spec_path, "+".join(sorted(features)) or "default")


def read_stream(stream: Path) -> FixpointProduct:
    """One kernel stream read back as the product it stands for. `enumerate-configs` writes plain ndjson, which `kernel_io.read_transitions` reads straight off the open handle — the gzip it wraps a path in is what the artifacts under `rebuild/out/` wear, not something a stream on its way into one fold needs. The file goes as soon as the product is in hand: a live configuration's stream is hundreds of megabytes and a whole cycle's worth would otherwise sit in the scratch directory for the length of the build."""
    with stream.open("rt", encoding="utf-8") as handle:
        product = kernel_io.read_transitions(handle)
    stream.unlink()
    return product


def enumerate_transitions(spec: ResolvedSpec, features: frozenset[str]) -> FixpointProduct:
    """One configuration's reachable windows, enumerated by the crate and parsed back into the value the stream is stated at. `table.FixpointProduct` carries everything the fold reads and nothing else the engine touched, which is why a consumer that wants what a table drops — the settled cells, the seams, the optimistic prospect, the fired provenance per row — asks for the product rather than for the tables. Nothing does any more: `build_tables` beside this one folds in the crate and never writes a stream, and no tool asks for one either, so what keeps the path exercised is `rebuild/test_kernel_exec.py`. Everything is in memory and nothing survives the call — the spec dump and the stream live in a scratch directory that goes with the frame."""
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
    """One configuration's decision and treaty tables, in memory and leaving nothing behind: one `build-tables` process over a scratch spec dump, then the windows payload and the treaty TSV read straight back. The rows come back whole here, unlike the build's own path, because a caller reaching for this wants the table rather than the artifacts — and it is a fixture-sized table, since the live alphabet's is what `run_m1.build_tables` writes. The partition replay, the deep-class union check and E-STRANDED are the crate's own raises as it folds, so a table handed back here has already passed them."""
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
