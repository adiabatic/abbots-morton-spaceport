//! `ams-kernel` is the command-line binary of the settlement kernel. It reads an `ams-spec/1` dump into the interned model and serves one subcommand per task, listed below: echoing the dump, settling windows for the Python callers, sweeping the late-formation guard, building and folding the tables, and checking the built tables by replay.
//!
//! **This crate is the definition of settlement.** The ranking, the refusals, the specificity order, the prospect, and the late-formation guard are implemented only here, so a settlement-semantics change is written here and nowhere else. Python defines the kernel interface. `engine/pipeline/kernel_io.py` defines the dump: its format is what `kernel_io.spec_json` writes, and its strictness is what `kernel_io.spec_of` enforces. `engine/pipeline/model.py` defines the fields a dump carries. `engine/pipeline/table.py` reads the files [`ams_kernel::fold`] and [`ams_kernel::artifacts`] write and computes their digests. `engine/pipeline/settle.py` keeps settlement's vocabulary and none of its logic: the token and boundary types, `cell_label`, and ligature formation, which runs before settlement and takes its verdicts from `guard-sweep`. Every other Python consumer (the conform sweep, the witness stage, explain, probe, and the review corpus) settles through `settle-cases`. `doc/engine-design.md` §14.1 records the measurements behind the port, including that packing the model into integer keys, not the change of language, gives the speedup, and that the hasher in [`ams_kernel::hash`] is faster than SipHash on these keys only with its finalizer.
//!
//! **A change to `engine/pipeline/model.py` must also be made in this crate.** The Python codec is driven by `dataclasses.fields`, so a new field enters the dump with no edit there. This crate lists its field sets by hand, so it rejects a dump with an unknown field instead of dropping the field. The spec-echo parity test in `engine/test_kernel_io.py`, which runs on every `make test-engine`, fails until the crate catches up.
//!
//! Three make targets build and check the crate. `make kernel-build` compiles the release binary the Python side runs. `make kernel-check` runs `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, and `cargo test`. `make kernel-gate` is the target to run after a kernel-semantics change; it runs `kernel-check` and nothing else. Beyond the crate's own tests, gate:conform checks settlement by shaping every swept text through HarfBuzz and comparing the result with this kernel's per-window settlement.
//!
//! The CLI takes positional arguments and scans flags by hand, with no argument parser. stdout carries the answer and nothing else. The three mode flags are written as negations of the default modes (`--candidacy-prospect`, `--follower-prefer-slots-off`, `--deep-classes-off`), so a bare invocation runs the default modes and every departure from them shows in the command line:
//!
//! - `ams-kernel spec-echo <spec>` writes the canonical dump plus one newline.
//! - `ams-kernel settle-cases <spec> <cases> [--features=a,b,…] [--settled-only] [--candidacy-prospect] [--follower-prefer-slots-off]` settles a plain-text case file, or standard input for `-`, through one engine, in file order. Each line of the input is one tab-separated window, as `kernel_exec.case_line` writes it. Each output line is the case line, a tab, and the case result: the whole trace as JSON (`kernel_exec.trace_of` reads it), or with `--settled-only` the settled record as seven tab-separated fields (`kernel_exec._settled_of_fields` reads it). A window that raises a settlement error gets an ordinary output line, the same `{"raise":…,"message":…}` object in either shape, and does not change the exit status.
//! - `ams-kernel guard-sweep <spec> [--config=<token>]` writes the section 5.7 guard verdict map, one tab-separated verdict per line. Without `--config=`, each verdict is quantified over the powerset of capability-unlock features, which is the map the font ships. With `--config=`, the map is answered under that one configuration, named by a token in `--configs=` form; `default` names the no-feature configuration, which an empty `--features=` could not. The engine suite compares each configuration's map with the quantified one. The guard fixes its own engine modes, so the two mode flags are a usage error here.
//! - `ams-kernel build-tables <spec> <outdir> --configs=a,b,… --inputs=<stamp> [--threads=N] [--no-default-memo-sharing] [--no-verdict-sharing] [--scratch-beside-default=N] [--prospect-memo-buckets=N] [--candidates-memo-buckets=N] [--trace-memo-buckets=N] [--no-trace-memo-skip] [--snapshot-cache-blocks=N] [--no-snapshot-filter] [--no-grouped-sweeps | --check-grouped-sweeps] [--previous=<config>=<base>:N,<segment>:N,…] [--windows=<config>=<path>] [--candidacy-prospect] [--follower-prefer-slots-off] [--deep-classes-off] [--timings] [--cache-stats]` runs each named configuration's table-build fixpoint and folds its product in memory, writing `<outdir>/settlement-<config>.tsv`, `<outdir>/joins-<config>.tsv`, and the uncompressed `<outdir>/windows-<config>.tsv` under the fingerprint `--inputs=` names, and one `{"config":…,"digest":…,"windows_bytes":…}` line per configuration to stdout in command-line order, where `windows_bytes` is how many bytes the configuration's window file took. `--windows=<config>=<path>`, given at most once a configuration and only for one `--configs=` names, writes that configuration's window file at `<path>` instead of under `<outdir>`. The path may name `/dev/fd/N`, a pipe the process inherits, as `kernel_exec.stream_payloads` hands one, so the payload reaches its reader through no path another process can open, and the reader checks the bytes it received against `windows_bytes`. `default` enumerates first and keeps its trace memo. The other configurations then run as deltas, `--threads` at a time, heaviest first by unlocking-rune count, with `default`'s fold preparation taking one of the worker slots. `--scratch-beside-default=N` starts up to `N` of the heaviest from scratch beside `default` instead, in whole tiers of equal unlocking-rune count and capped at one less than `--threads`, so they read no memo of `default`'s and need not wait for it, and the rest run as deltas once `default` has enumerated. Before any table is written, the configurations exchange the windows each keeps live that another's rules would answer wrongly in the order the font ships ([`ams_kernel::crossconfig`]), so a configuration's tables depend on the set `--configs=` names. Each delta reads `default`'s memo for every window that names none of its own unlocking runes ([`ams_kernel::memo`]) and writes the same tables a from-scratch enumeration writes. `--no-default-memo-sharing` enumerates every configuration from scratch, and a set without `default` does so anyway. Any configuration enumerated from scratch writes the same tables. When some delta reads `default`'s memo, `default` also publishes its deep-slot liveness verdicts there, and a delta served one reruns only the probe's asks that name one of its unlocking runes ([`ams_kernel::liveness`]); `--no-verdict-sharing` runs every probe whole, which writes the same tables. The fourth-slot prospect probe, wherever it runs whole, answers an ask from the transcript an earlier ask of the same probe recorded when the ask's token answers every question in it alike, instead of settling the ask's own window again ([`ams_kernel::liveness`]). `--no-grouped-sweeps` runs every ask in full instead, and `--check-grouped-sweeps` groups the asks and also runs every ask a transcript answered in full, exiting 1 with a message naming the probe and the two tokens when the full ask returns another result or fires a pointer the build had not. All three write the same tables and digests, and the two flags cannot be given together. A memo lives only inside the process: `default` keeps its finished memo while a delta is left to read it, every other configuration releases its own before it drains and sorts its rows, and the build reads no memo file and writes none. Each configuration's engine clears its prospect memo in place once it holds what a table of 2^20 buckets holds, and its candidate memo at 2^22 buckets ([`ams_kernel::fixpoint::TABLE_MEMO_CEILINGS`]); `--prospect-memo-buckets=N` and `--candidates-memo-buckets=N` set other ceilings, each a power of two of at least eight. Each configuration whose memo no delta reads, every delta and `default` once every delta starts from scratch, holds its trace memo's sixteen shards to 2^25 buckets in all, clearing a shard whose next insert would double its table, and stores none of the windows with a concrete third slot and a known fourth ([`ams_kernel::fixpoint::TABLE_TRACE_CEILING`]); `--trace-memo-buckets=N` sets another ceiling, a power of two of at least 128, and `--no-trace-memo-skip` stores those windows too. Any ceiling writes the same tables and digests. While a delta reads it, `default` packs its memo at its release into sorted blocks of 512 records behind fences and a negative filter, and each delta's engine keeps a cache of 455 decoded blocks of it ([`ams_kernel::memo::SnapshotEntries`]); `--snapshot-cache-blocks=N` sets another cache, zero for none, under which a lookup decodes its block in place, and `--no-snapshot-filter` builds no filter. Either writes the same tables and digests. `--previous=<config>=<base>:N,<segment>:N,…`, given at most once a configuration and only for one `--configs=` names, lists that configuration's previous windows rows, its base and then its segments in order, each a path and the rows its head lists: the base's window rows, and each segment's upserts and tombstones, with every header line stripped ([`ams_kernel::row_merge`]). Each path may name `/dev/fd/N`, a pipe the process inherits, as `run_build.build_tables` hands them, and each is opened and read once, front to back, at that configuration's finish. Its `windows-<config>.tsv` then holds its head line and only the rows that differ from the merged previous rows, an upsert for a new key or a changed outcome and a tombstone for a key that is gone, with no column line ([`ams_kernel::artifacts::write_windows`]); a part whose keys do not strictly ascend or whose rows are not the count it names exits 1 naming the part, and the digest is over the table's own rows either way. `run_build.build_tables` gzips the windows payload into the next base or segment of the configuration's windows output and keeps its head line as the output's head file, because the crate has no compressor. The directory is created if needed and nothing in it is deleted, because a build writes into its artifact directory beside other artifacts. `--inputs=` is required because a persisted enumeration is accepted or rejected on the stamp it carries. `<spec>` may name `/dev/fd/N`, a pipe the process inherits from a caller that writes the dump into it, as `kernel_exec.build_table_files` hands it; the spec is read once, front to back, before any output is opened.
//! - `ams-kernel replay-strings <spec> <outdir> --configs=a,b,… --max-length=N [--families=a,b,…] [--last=U+XXXX] [--memo-file=<path> | --memo-windows=N] [--threads=N] [--candidacy-prospect] [--follower-prefer-slots-off] [--timings] [--cache-stats]` reads each configuration's `<outdir>/settlement-<config>.tsv` and walks every text of length 1 through `N` over the spec's alphabet ([`ams_kernel::replay`]), one last symbol at a time: the texts ending in each symbol are a unit, after which the walk releases its memos, keeping only the windows a later unit can meet. With `--families=`, it walks only the texts that name one of those runes; a ligature is named through its components. With `--last=`, it walks only the texts whose last symbol is the alphabet symbol at that code point, a letter or a boundary token, written as `U+` and four to six uppercase hexadecimal digits; a code point outside the alphabet exits 1. The walks over every symbol partition the texts between them, as a walk's own units do, and `skipped` counts only the texts ending in that symbol that `--families=` leaves out; such a walk is one unit and releases nothing after it. It applies the rules first-match, feeding each settled left forward, and compares every window's rule outcome with this engine's settlement of that window. `run_build` runs it after every table build as the enumeration-completeness check. A clean run writes one `{"config":…,"texts":…,"windows":…,"skipped":…}` line per configuration to stdout. `--memo-file=<path>`, for a run that names one configuration, has the walk keep a row for every window it settles and then write a passing walk's window memo at `<path>`, the input to the build's settle memo ([`ams_kernel::replay::Replay::write_window_memo`]). The path may name `/dev/fd/N`, a pipe the process inherits: `run_build` hands each walk one (`kernel_exec.replay_memo_walk`) and reads it as the rows arrive; a reader that closes it before the end stops the write, and the run still answers on stdout and exits 0, with nothing on stderr. `--memo-windows=N` caps each walk's memos at `N` windows, or at one text's windows when `N` is below the maximum length: before any text that could push them past the cap, the walk releases its memos and its engine's memos. The walk's memory then does not grow with the size of the text set, apart from the settled records and labels it keeps, and `windows` counts window settles instead of distinct windows. `--memo-windows=` with `--memo-file=` is a usage error, because a released memo is incomplete. A window where the rules and the engine disagree, or that the engine refuses, exits 1 with a message naming the configuration, the window, and the text it was reached in. The maximum length is required because the caller records the depth the walk covered. There is no grain flag, because a replay settles single windows, which have no grain.
//! - `ams-kernel replay-emitted <windows> --config=<token> --table=<settlement.tsv> --order=<order.tsv> --order-rules=N --context=<context.tsv> [--base-rows=N [--segments=<segment>:N,…]] [--timings]` walks one configuration's window enumeration (the plain `ams-windows/3` payload at `<windows>`, or standard input for `-`) against the settlement order the font ships ([`ams_kernel::shipped_order`]). With `--base-rows=`, `<windows>` is a base of the configuration's windows output, whose rows after its head and column lines must be that many, and `--segments=` lists the segments to merge over it in order, each a path, `/dev/fd/N` as `kernel_exec.replay_emitted` hands them, and the rows its head lists, with its header line stripped; the walk reads the base's head and column lines and then the merged rows in key order ([`ams_kernel::row_merge::MergedPayload`]), so it walks what the whole payload holds and answers as it would, and a part whose keys do not strictly ascend or whose rows are not its count exits 1 naming the part. `--segments=` without `--base-rows=` is a usage error. `--order=` holds every configuration's rules in the order the emitter ships them, written as a settlement TSV whose provenance column names the table rules each row was folded from, and `--order-rules=` is how many rules it holds: an order that parses to any other count exits 1 naming both, so an order that ends short at a line boundary is refused instead of walked as a shorter one. `--order=` and `--context=` may name `/dev/fd/N`, a pipe the process inherits from a caller that writes into it, as `kernel_exec.replay_emitted` hands both; the table, the order and the context are each read once, front to back, in that order, before the windows. `--context=` holds the configuration's marker renames, deep classes, and foreign labels (the bare runes, marker copies and locked copies its rows never carry, as `emit_gsub.emitted_context_tsv` lists them), one `rename`, `class`, or `foreign` record per line. `--table=` is the configuration's own settlement TSV, read only to name the table's rule in a disagreement. Each row is renamed through the configuration's marker renames, and the first emitted rule for its input that matches it must give the row's outcome, on the row itself and on every continuation of a `#NA` it leaves open after a letter. A clean run writes one `{"config":…,"rows":…,"checked_per_member":…,"open_rows":…,"continued":…}` line to stdout, where `checked_per_member` counts the rows tried member by member because an emitted lookahead class matched only part of their deep class, `open_rows` the rows that leave a slot open after a letter, and `continued` those whose continuations the walk searched because their deciding rule constrains the open slot or a later one. A row for which the shipped order gives a different outcome exits 1 with a message naming the configuration, the row, the continuation, the emitted rule that fired, and the table's rule; so does a row that carries a foreign label. No spec is read: the tables already hold the settled answers, and the walk checks that the shipped lookup reproduces them.
//!
//! Concurrency is per configuration. `build-tables` and `replay-strings` run at most `--threads` configurations at once: one when the flag is absent, and never more than the machine's available parallelism or the number of configurations. When the set includes `default` and `--no-default-memo-sharing` is not given, `build-tables` enumerates `default` first, beside the configurations `--scratch-beside-default=` starts from scratch, and then runs the other configurations at that width, with `default`'s fold preparation in one of the worker slots, and holds every configuration's prepared product until the exchange ends. [`ams_kernel::fanout`] explains why the output bytes do not depend on the schedule and why one configuration's worklist stays sequential. Peak memory grows roughly linearly with the width, because each configuration in flight holds its whole working set, so a machine with less memory than cores should pass a smaller `--threads`.
//!
//! `--cache-stats` is accepted by `build-tables` and `replay-strings`. It writes `[c] <config> <collection> len=<n> cap=<m>` lines to stderr, one per memo or table, plus the size of the elimination text the memos hold and the process's resident size at several points. [`ams_kernel::fixpoint::enumerate_for_tables`] lists the enumeration's sample points, among them what its row log holds (`row_log bytes= fragments= revisited_bases=`) and how many times its engine cleared the prospect and candidate memos at their ceilings and a trace memo shard made room at its own (`memo_clears prospect= candidates= trace=`), what its grouped sweeps did whenever they are on (`grouped_sweeps asks= matched_simulated= matched_estimate= full= kept_simulated= kept_estimate= poisoned_rune= poisoned_letter= poisoned_ordinal= poisoned_letter_ordinal= poisoned_compare= answered_from_memo= unled= raised= capture_open= max_transcripts= max_questions= live_after_match= prospect_inserts=`, `Engine::sweep_stats_line`), a table build adds the snapshot `default` packs for its deltas (`snapshot records= blocks= packed_bytes= fence_bytes= filter_bytes= pool_bytes= sort_ms= encode_ms=`), each delta's reads of it (`shared_memo_store id= gets= filtered= cached= decoded= cache_bytes=`), what each configuration's parked product holds (`parked bytes= class_rows= fold_rows= stretches=`), `resident_parked` once every configuration has parked its prepared fold product, and the heap once every configuration has parked and once every configuration has read its product back (`parked_heap`, `read_back_heap`; `fanout::table_thread`), and [`ams_kernel::replay::Replay::take_cache_stats`] what the replay reports when a walk ends, including the side memo, the release count (`releases count=`), the unit count (`units count=`) and what the row store holds (`row_store rows= bytes= chunks=`). A walk with no `--last=` adds memo sizes and resident sizes after each unit, labeled `unit=<k>`, and under `--memo-windows=` each release adds them labeled `release=<k>`. A table enumeration whose memo no other enumeration reads and which publishes no verdicts journals no reads and no deltas ([`ams_kernel::engine::Engine::set_read_journal`], [`ams_kernel::engine::Engine::set_delta_journal`]), so its `reads` and `deltas` lines each report the empty entry alone, `len=1`. Memory decisions about this crate are made from these lines, because arithmetic on struct definitions only estimates what a run with `--cache-stats` measures. The cache stats cost nothing when they are not requested and never change stdout, the tables, or the replay's answer lines. Their lines go into the same buffer as `--timings` and are written in `--configs` order. The two flags are independent.
//!
//! `--timings` is accepted by `build-tables`, `replay-strings`, and `replay-emitted`. It writes `[t] <label> <secs>s` lines to stderr at one decimal place, the format `engine/tools/console.py` defines and `engine/tools/cycle_timings.py` parses. The lines are `spec_parse` (reading, parsing, and indexing the spec) where a spec is read, then each configuration's phases (such as `enumerate[<config>]`, `fold[<config>]`, or `replay[<config>]`), then the run's total. A table build also reports `fold.prefixes[<config>]`, `fold.exchange[<config>]` and `fold.partition[<config>]` at millisecond precision before each `fold[<config>]` line. Every line is buffered and written after the last configuration finishes, in `--configs` order, so stderr is the same at any thread count. With neither diagnostic flag, nothing is written to stderr on a clean exit, because `kernel_exec` treats any stderr on a clean exit as a failure unless it asked for timings.
//!
//! A usage mistake (a wrong argument count, an unknown subcommand, an unknown flag, a flag the subcommand does not accept, or an argument that is not valid Unicode) exits 2. An input file that cannot be read, parsed, or validated, a directory that cannot be written, a case file this build cannot answer, and a window that will not settle exit 1 with a one-line message on stderr.

#![forbid(unsafe_code)]

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{Duration, Instant};

use ams_kernel::emit::json_string;
use ams_kernel::engine::{Engine, EngineModes, GroupedSweeps, MemoCeilings, TraceCeiling};
use ams_kernel::fixpoint::{EnumerationModes, TABLE_MEMO_CEILINGS, TABLE_TRACE_CEILING};
use ams_kernel::index::SpecIndex;
use ams_kernel::memo::{SNAPSHOT_CACHE_BLOCKS, SnapshotPacking};
use ams_kernel::model::Sym;
use ams_kernel::stream::DEFAULT_CONFIG;
use ams_kernel::{artifacts, cases, emit, fanout, guard, parse, row_merge, shipped_order};

const USAGE: &str = "usage: ams-kernel spec-echo <spec>\n       ams-kernel settle-cases <spec> <cases> [--features=a,b] [--settled-only] [--candidacy-prospect] [--follower-prefer-slots-off]\n       ams-kernel guard-sweep <spec> [--config=default|ss03+ss05]\n       ams-kernel build-tables <spec> <outdir> --configs=default,ss03 --inputs=<stamp> [--threads=N] [--no-default-memo-sharing] [--no-verdict-sharing] [--scratch-beside-default=N] [--prospect-memo-buckets=N] [--candidates-memo-buckets=N] [--trace-memo-buckets=N] [--no-trace-memo-skip] [--snapshot-cache-blocks=N] [--no-snapshot-filter] [--no-grouped-sweeps | --check-grouped-sweeps] [--previous=<config>=<base>:N,<segment>:N] [--windows=<config>=<path>] [--candidacy-prospect] [--follower-prefer-slots-off] [--deep-classes-off] [--timings] [--cache-stats]\n       ams-kernel replay-strings <spec> <outdir> --configs=default,ss03 --max-length=N [--families=qsPea,qsTea] [--last=U+E650] [--memo-file=<path> | --memo-windows=N] [--threads=N] [--candidacy-prospect] [--follower-prefer-slots-off] [--timings] [--cache-stats]\n       ams-kernel replay-emitted <windows> --config=default --table=<settlement.tsv> --order=<order.tsv> --order-rules=N --context=<context.tsv> [--base-rows=N [--segments=<segment>:N,<segment>:N]] [--timings]";

/// The flags and positionals a command line named, before the subcommand checks its positional count. The three mode flags are written as negations because the default modes turn all three on.
struct Flags<'a> {
    positionals: Vec<&'a str>,
    features: Vec<&'a str>,
    configs: Option<Vec<&'a str>>,
    config: Option<&'a str>,
    inputs: Option<&'a str>,
    threads: Option<usize>,
    max_length: Option<usize>,
    families: Option<Vec<&'a str>>,
    last: Option<i64>,
    memo_file: Option<&'a str>,
    memo_windows: Option<usize>,
    timings: bool,
    cache_stats: bool,
    default_memo_sharing: bool,
    verdict_sharing: bool,
    scratch_beside_default: Option<usize>,
    prospect_memo_buckets: Option<usize>,
    candidates_memo_buckets: Option<usize>,
    trace_memo_buckets: Option<usize>,
    trace_memo_skip: bool,
    snapshot_cache_blocks: Option<usize>,
    snapshot_filter: bool,
    grouped_sweeps: GroupedSweeps,
    table: Option<&'a str>,
    order: Option<&'a str>,
    order_rules: Option<usize>,
    context: Option<&'a str>,
    base_rows: Option<u64>,
    segments: Option<&'a str>,
    previous: Vec<(&'a str, &'a str)>,
    windows: Vec<(&'a str, &'a str)>,
    settled_only: bool,
    simulated_prospect: bool,
    follower_prefer_slots: bool,
    deep_classes: bool,
}

/// Which optional flags a subcommand accepts. [`scan_flags`] treats any other flag as unknown, so `--configs=` on `settle-cases` is a usage error, and so is `--features=` on `build-tables`, where the configuration tokens name the features.
#[derive(Clone, Copy)]
struct Vocabulary {
    grain: bool,
    features: bool,
    /// `--configs=` and `--threads=`, for the subcommands that run a set of configurations.
    configs: bool,
    /// `--config=`, one configuration token in `--configs=` form.
    config: bool,
    /// `--inputs=`, the fingerprint stamp written into a windows head. Only `build-tables` writes one.
    inputs: bool,
    /// `--timings` and `--cache-stats`, the two stderr diagnostics.
    timings: bool,
    /// The string replay's `--max-length=`, `--families=`, `--last=`, `--memo-file=`, and `--memo-windows=`: how deep to walk, which runes' texts to walk, which symbol they end in, where to write the one walk's window memo, and the most windows a walk keeps memoized.
    max_length: bool,
    /// `--table=`, `--order=`, and `--context=`, the three files of `replay-emitted`, and `--order-rules=`, the order's rule count, which it requires beside the three and `--config=`; and `--base-rows=` and `--segments=`, the windows' base's row count and the segments merged over it.
    emitted: bool,
    /// `--previous=`, one configuration's previous windows rows, which only `build-tables` merges against, and `--windows=`, the path one configuration's window file goes to, which only `build-tables` writes.
    previous: bool,
    /// The table build's memo flags. `--no-default-memo-sharing` enumerates every configuration from scratch instead of reading `default`'s finished memo for the windows a configuration shares with it, and `--no-verdict-sharing` reads its windows but has `default` publish no liveness verdicts. `--scratch-beside-default=` names at most how many of the heaviest deltas enumerate from scratch beside `default`, `--prospect-memo-buckets=` and `--candidates-memo-buckets=` set the ceilings on each engine's prospect and candidate memos, and `--trace-memo-buckets=` and `--no-trace-memo-skip` the ceiling on the trace memo of each engine whose memo no other reads, and `--snapshot-cache-blocks=` and `--no-snapshot-filter` how many of `default`'s snapshot's decoded blocks each delta caches and whether the snapshot is packed with a filter, and `--no-grouped-sweeps` and `--check-grouped-sweeps` whether the fourth-slot prospect probes group their sweeps, which they do unless `--no-grouped-sweeps` is given ([`ams_kernel::fanout::MemoSharing`]).
    memo_flags: bool,
    /// `--settled-only`: make each case result the settled record as seven tab-separated fields instead of the whole trace. Only `settle-cases` returns a trace.
    settled: bool,
    /// `-` as a positional, naming standard input as the file the subcommand reads line by line: the case file of `settle-cases` and the windows of `replay-emitted`.
    stdin: bool,
}

/// The flag sets of the subcommands that take flags.
const CASES_FLAGS: Vocabulary = Vocabulary {
    grain: false,
    features: true,
    config: false,
    configs: false,
    inputs: false,
    timings: false,
    max_length: false,
    previous: false,
    emitted: false,
    memo_flags: false,
    settled: true,
    stdin: true,
};
const TABLES_FLAGS: Vocabulary = Vocabulary {
    grain: true,
    features: false,
    config: false,
    configs: true,
    inputs: true,
    timings: true,
    max_length: false,
    previous: true,
    emitted: false,
    memo_flags: true,
    settled: false,
    stdin: false,
};
/// The string replay accepts the fan-out's configuration flags, both stderr diagnostics, and the walk's own flags (`Vocabulary::max_length`). It has no grain flag, because it settles single windows, and no stamp, because the one file it writes (the window memo at `--memo-file=`) is a build input and not an artifact.
const REPLAY_FLAGS: Vocabulary = Vocabulary {
    grain: false,
    features: false,
    config: false,
    configs: true,
    inputs: false,
    timings: true,
    max_length: true,
    previous: false,
    emitted: false,
    memo_flags: false,
    settled: false,
    stdin: false,
};
/// `guard-sweep` takes one configuration or none. Its engine modes are fixed in `guard.rs`, so [`plan_guard`] rejects the mode flags that [`scan_flags`] accepts for every subcommand.
const GUARD_FLAGS: Vocabulary = Vocabulary {
    grain: false,
    features: false,
    config: true,
    configs: false,
    inputs: false,
    timings: false,
    max_length: false,
    previous: false,
    emitted: false,
    memo_flags: false,
    settled: false,
    stdin: false,
};

/// `replay-emitted` takes one configuration, its three files, the order's rule count, and `--timings`. It settles nothing, so [`plan_emitted`] rejects the mode flags and `--cache-stats`.
const EMITTED_FLAGS: Vocabulary = Vocabulary {
    grain: false,
    features: false,
    config: true,
    configs: false,
    inputs: false,
    timings: true,
    max_length: false,
    previous: false,
    emitted: true,
    memo_flags: false,
    settled: false,
    stdin: true,
};

/// What a `settle-cases` command line asked for.
struct CasesPlan<'a> {
    spec: &'a str,
    cases: &'a str,
    features: Vec<&'a str>,
    settled_only: bool,
    simulated_prospect: bool,
    follower_prefer_slots: bool,
}

/// What a `guard-sweep` command line asked for. `config` is `None` for the map quantified over the powerset.
struct GuardPlan<'a> {
    spec: &'a str,
    config: Option<ConfigRequest<'a>>,
}

/// One configuration a command line named. `token` is also the configuration's part of each artifact filename, its windows head's `config`, and the label of its timing lines; `features` is what the token parses into.
struct ConfigRequest<'a> {
    token: &'a str,
    features: Vec<&'a str>,
}

/// What a `build-tables` command line asked for: the spec, the output directory, the configurations named by tokens, the thread count, the mode flags and the two diagnostics, the fingerprint stamp written into every windows head, the memo flags, and, in `configs` order, each configuration's previous windows rows, empty for a configuration with none, and the path its window file goes to, `None` for one the command line names none for.
struct TablesPlan<'a> {
    spec: &'a str,
    outdir: &'a str,
    configs: Vec<ConfigRequest<'a>>,
    inputs: &'a str,
    threads: Option<usize>,
    default_memo_sharing: bool,
    verdict_sharing: bool,
    scratch_beside_default: usize,
    memo_ceilings: MemoCeilings,
    trace_ceiling: TraceCeiling,
    snapshot_packing: SnapshotPacking,
    snapshot_cache_blocks: usize,
    grouped_sweeps: GroupedSweeps,
    simulated_prospect: bool,
    follower_prefer_slots: bool,
    deep_classes: bool,
    timings: bool,
    cache_stats: bool,
    previous: Vec<Vec<row_merge::PriorPart>>,
    windows: Vec<Option<&'a str>>,
}

/// What a `replay-strings` command line asked for. `outdir` is the directory the tables are read from.
struct ReplayPlan<'a> {
    spec: &'a str,
    outdir: &'a str,
    configs: Vec<ConfigRequest<'a>>,
    max_length: usize,
    families: Option<Vec<&'a str>>,
    last: Option<i64>,
    memo_file: Option<&'a str>,
    memo_windows: Option<usize>,
    threads: Option<usize>,
    simulated_prospect: bool,
    follower_prefer_slots: bool,
    timings: bool,
    cache_stats: bool,
}

/// What a `replay-emitted` command line asked for. `windows` is `-` for standard input. With `base_rows`, `windows` is a base of that many rows and `segments` the segments merged over it.
struct EmittedPlan<'a> {
    windows: &'a str,
    config: &'a str,
    table: &'a str,
    order: &'a str,
    order_rules: usize,
    context: &'a str,
    base_rows: Option<u64>,
    segments: Vec<row_merge::PriorPart>,
    timings: bool,
}

fn main() -> ExitCode {
    let Ok(arguments) = std::env::args_os()
        .skip(1)
        .map(std::ffi::OsString::into_string)
        .collect::<Result<Vec<String>, _>>()
    else {
        return usage();
    };
    let Some((command, rest)) = arguments.split_first() else {
        return usage();
    };
    let outcome = match command.as_str() {
        "spec-echo" => {
            let [path] = rest else {
                return usage();
            };
            spec_echo(path)
        }
        "settle-cases" => {
            let Some(plan) = plan_cases(rest) else {
                return usage();
            };
            settle_cases(&plan)
        }
        "guard-sweep" => {
            let Some(plan) = plan_guard(rest) else {
                return usage();
            };
            guard_sweep(&plan)
        }
        "build-tables" => {
            let Some(plan) = plan_tables(rest) else {
                return usage();
            };
            build_tables(&plan)
        }
        "replay-strings" => {
            let Some(plan) = plan_replay(rest) else {
                return usage();
            };
            replay_strings(&plan)
        }
        "replay-emitted" => {
            let Some(plan) = plan_emitted(rest) else {
                return usage();
            };
            replay_emitted(&plan)
        }
        _ => return usage(),
    };
    match outcome {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("ams-kernel: {error}");
            ExitCode::from(1)
        }
    }
}

fn usage() -> ExitCode {
    eprintln!("{USAGE}");
    ExitCode::from(2)
}

/// Scans the arguments for every subcommand, returning `None` on a usage error. A flag outside `vocabulary` is unknown, and a value flag given twice is a usage error.
///
/// An empty `--features=` is a usage error, not the no-feature configuration: `kernel_exec` omits the flag when no feature is active, so an empty value means the two sides disagree about the flags. An empty `--configs=` is a usage error because a run needs at least one configuration. A count (`--threads=`, `--max-length=`, `--memo-windows=`, `--scratch-beside-default=`, `--prospect-memo-buckets=`, `--candidates-memo-buckets=`, `--trace-memo-buckets=`) must be ASCII digits with a positive value that fits in `usize`, so `+3`, which `usize`'s parser would accept, is rejected. `--order-rules=` and `--base-rows=` are row counts and `--snapshot-cache-blocks=` a cache's size, so they are held to the same spelling but may be zero. A part list (`--segments=`, and `--previous=` after its configuration token and `=`) is kept as written here and parsed by the subcommand's plan, `<path>:<rows>` items joined by commas ([`ams_kernel::row_merge::parse_parts`]), and `--previous=` may be given once a configuration. A code point (`--last=`) has the one spelling [`codepoint_flag`] reads.
fn scan_flags(rest: &[String], vocabulary: Vocabulary) -> Option<Flags<'_>> {
    let mut positionals: Vec<&str> = Vec::new();
    let mut features: Option<Vec<&str>> = None;
    let mut configs: Option<Vec<&str>> = None;
    let mut config: Option<&str> = None;
    let mut inputs: Option<&str> = None;
    let mut threads: Option<usize> = None;
    let mut max_length: Option<usize> = None;
    let mut families: Option<Vec<&str>> = None;
    let mut last: Option<i64> = None;
    let mut memo_file: Option<&str> = None;
    let mut memo_windows: Option<usize> = None;
    let mut timings = false;
    let mut cache_stats = false;
    let mut default_memo_sharing = true;
    let mut verdict_sharing = true;
    let mut scratch_beside_default: Option<usize> = None;
    let mut prospect_memo_buckets: Option<usize> = None;
    let mut candidates_memo_buckets: Option<usize> = None;
    let mut trace_memo_buckets: Option<usize> = None;
    let mut trace_memo_skip = true;
    let mut snapshot_cache_blocks: Option<usize> = None;
    let mut snapshot_filter = true;
    let mut grouped_sweeps = GroupedSweeps::On;
    let mut table: Option<&str> = None;
    let mut order: Option<&str> = None;
    let mut order_rules: Option<usize> = None;
    let mut context: Option<&str> = None;
    let mut base_rows: Option<u64> = None;
    let mut segments: Option<&str> = None;
    let mut previous: Vec<(&str, &str)> = Vec::new();
    let mut windows: Vec<(&str, &str)> = Vec::new();
    let mut settled_only = false;
    let mut simulated_prospect = true;
    let mut follower_prefer_slots = true;
    let mut deep_classes = true;
    for argument in rest {
        if argument == "--candidacy-prospect" {
            simulated_prospect = false;
        } else if argument == "--follower-prefer-slots-off" {
            follower_prefer_slots = false;
        } else if vocabulary.grain && argument == "--deep-classes-off" {
            deep_classes = false;
        } else if vocabulary.memo_flags && argument == "--no-default-memo-sharing" {
            default_memo_sharing = false;
        } else if vocabulary.memo_flags && argument == "--no-verdict-sharing" {
            verdict_sharing = false;
        } else if vocabulary.memo_flags
            && let Some(count) = argument.strip_prefix("--scratch-beside-default=")
        {
            if scratch_beside_default.is_some() || !count.bytes().all(|byte| byte.is_ascii_digit())
            {
                return None;
            }
            scratch_beside_default = Some(count.parse::<usize>().ok().filter(|count| *count > 0)?);
        } else if vocabulary.memo_flags
            && let Some(count) = argument.strip_prefix("--prospect-memo-buckets=")
        {
            if prospect_memo_buckets.is_some() || !count.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            prospect_memo_buckets = Some(count.parse::<usize>().ok().filter(|count| *count > 0)?);
        } else if vocabulary.memo_flags
            && let Some(count) = argument.strip_prefix("--candidates-memo-buckets=")
        {
            if candidates_memo_buckets.is_some() || !count.bytes().all(|byte| byte.is_ascii_digit())
            {
                return None;
            }
            candidates_memo_buckets = Some(count.parse::<usize>().ok().filter(|count| *count > 0)?);
        } else if vocabulary.memo_flags
            && let Some(count) = argument.strip_prefix("--trace-memo-buckets=")
        {
            if trace_memo_buckets.is_some() || !count.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            trace_memo_buckets = Some(count.parse::<usize>().ok().filter(|count| *count > 0)?);
        } else if vocabulary.memo_flags && argument == "--no-trace-memo-skip" {
            trace_memo_skip = false;
        } else if vocabulary.memo_flags
            && let Some(count) = argument.strip_prefix("--snapshot-cache-blocks=")
        {
            if snapshot_cache_blocks.is_some() || !count.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            snapshot_cache_blocks = Some(count.parse::<usize>().ok()?);
        } else if vocabulary.memo_flags && argument == "--no-snapshot-filter" {
            snapshot_filter = false;
        } else if vocabulary.memo_flags && argument == "--no-grouped-sweeps" {
            if grouped_sweeps == GroupedSweeps::Check {
                return None;
            }
            grouped_sweeps = GroupedSweeps::Off;
        } else if vocabulary.memo_flags && argument == "--check-grouped-sweeps" {
            if grouped_sweeps == GroupedSweeps::Off {
                return None;
            }
            grouped_sweeps = GroupedSweeps::Check;
        } else if vocabulary.emitted
            && let Some(path) = argument.strip_prefix("--table=")
        {
            if path.is_empty() || table.is_some() {
                return None;
            }
            table = Some(path);
        } else if vocabulary.emitted
            && let Some(path) = argument.strip_prefix("--order=")
        {
            if path.is_empty() || order.is_some() {
                return None;
            }
            order = Some(path);
        } else if vocabulary.emitted
            && let Some(count) = argument.strip_prefix("--order-rules=")
        {
            if order_rules.is_some() || !count.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            order_rules = Some(count.parse::<usize>().ok()?);
        } else if vocabulary.emitted
            && let Some(path) = argument.strip_prefix("--context=")
        {
            if path.is_empty() || context.is_some() {
                return None;
            }
            context = Some(path);
        } else if vocabulary.emitted
            && let Some(count) = argument.strip_prefix("--base-rows=")
        {
            if base_rows.is_some() || !count.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            base_rows = Some(count.parse::<u64>().ok()?);
        } else if vocabulary.emitted
            && let Some(list) = argument.strip_prefix("--segments=")
        {
            if segments.is_some() {
                return None;
            }
            segments = Some(list);
        } else if vocabulary.previous
            && let Some(named) = argument.strip_prefix("--previous=")
        {
            let (token, list) = named.split_once('=')?;
            if token.is_empty() || previous.iter().any(|(named, _)| *named == token) {
                return None;
            }
            previous.push((token, list));
        } else if vocabulary.previous
            && let Some(named) = argument.strip_prefix("--windows=")
        {
            let (token, path) = named.split_once('=')?;
            if token.is_empty()
                || path.is_empty()
                || windows.iter().any(|(named, _)| *named == token)
            {
                return None;
            }
            windows.push((token, path));
        } else if vocabulary.settled && argument == "--settled-only" {
            settled_only = true;
        } else if vocabulary.timings && argument == "--timings" {
            timings = true;
        } else if vocabulary.timings && argument == "--cache-stats" {
            cache_stats = true;
        } else if vocabulary.features
            && let Some(list) = argument.strip_prefix("--features=")
        {
            if list.is_empty() || features.is_some() {
                return None;
            }
            features = Some(list.split(',').collect());
        } else if vocabulary.configs
            && let Some(list) = argument.strip_prefix("--configs=")
        {
            if list.is_empty() || configs.is_some() {
                return None;
            }
            configs = Some(list.split(',').collect());
        } else if vocabulary.config
            && let Some(token) = argument.strip_prefix("--config=")
        {
            if token.is_empty() || config.is_some() {
                return None;
            }
            config = Some(token);
        } else if vocabulary.inputs
            && let Some(stamp) = argument.strip_prefix("--inputs=")
        {
            if stamp.is_empty() || inputs.is_some() {
                return None;
            }
            inputs = Some(stamp);
        } else if vocabulary.configs
            && let Some(count) = argument.strip_prefix("--threads=")
        {
            if threads.is_some() || !count.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            threads = Some(count.parse::<usize>().ok().filter(|count| *count > 0)?);
        } else if vocabulary.max_length
            && let Some(depth) = argument.strip_prefix("--max-length=")
        {
            if max_length.is_some() || !depth.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            max_length = Some(depth.parse::<usize>().ok().filter(|depth| *depth > 0)?);
        } else if vocabulary.max_length
            && let Some(list) = argument.strip_prefix("--families=")
        {
            if list.is_empty() || families.is_some() {
                return None;
            }
            families = Some(list.split(',').collect());
        } else if vocabulary.max_length
            && let Some(codepoint) = argument.strip_prefix("--last=")
        {
            if last.is_some() {
                return None;
            }
            last = Some(codepoint_flag(codepoint)?);
        } else if vocabulary.max_length
            && let Some(path) = argument.strip_prefix("--memo-file=")
        {
            if path.is_empty() || memo_file.is_some() {
                return None;
            }
            memo_file = Some(path);
        } else if vocabulary.max_length
            && let Some(count) = argument.strip_prefix("--memo-windows=")
        {
            if memo_windows.is_some() || !count.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            memo_windows = Some(count.parse::<usize>().ok().filter(|count| *count > 0)?);
        } else if vocabulary.stdin && argument == "-" {
            positionals.push(argument.as_str());
        } else if argument.starts_with('-') {
            return None;
        } else {
            positionals.push(argument.as_str());
        }
    }
    Some(Flags {
        positionals,
        features: features.unwrap_or_default(),
        configs,
        config,
        inputs,
        threads,
        max_length,
        families,
        last,
        memo_file,
        memo_windows,
        timings,
        cache_stats,
        default_memo_sharing,
        verdict_sharing,
        scratch_beside_default,
        prospect_memo_buckets,
        candidates_memo_buckets,
        trace_memo_buckets,
        trace_memo_skip,
        snapshot_cache_blocks,
        snapshot_filter,
        grouped_sweeps,
        table,
        order,
        order_rules,
        context,
        base_rows,
        segments,
        previous,
        windows,
        settled_only,
        simulated_prospect,
        follower_prefer_slots,
        deep_classes,
    })
}

/// A `--last=` value: `U+` and four to six uppercase hexadecimal digits, the one form `kernel_exec` writes, or `None` for any other spelling.
fn codepoint_flag(text: &str) -> Option<i64> {
    let digits = text.strip_prefix("U+")?;
    if !(4..=6).contains(&digits.len())
        || !digits
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'A'..=b'F').contains(&byte))
    {
        return None;
    }
    i64::from_str_radix(digits, 16).ok()
}

fn plan_cases(rest: &[String]) -> Option<CasesPlan<'_>> {
    let flags = scan_flags(rest, CASES_FLAGS)?;
    let [spec, cases] = flags.positionals.as_slice() else {
        return None;
    };
    Some(CasesPlan {
        spec,
        cases,
        features: flags.features,
        settled_only: flags.settled_only,
        simulated_prospect: flags.simulated_prospect,
        follower_prefer_slots: flags.follower_prefer_slots,
    })
}

fn plan_guard(rest: &[String]) -> Option<GuardPlan<'_>> {
    let flags = scan_flags(rest, GUARD_FLAGS)?;
    if !flags.simulated_prospect || !flags.follower_prefer_slots {
        return None;
    }
    let [spec] = flags.positionals.as_slice() else {
        return None;
    };
    let config = match flags.config {
        Some(token) => Some(config_requests(vec![token])?.pop()?),
        None => None,
    };
    Some(GuardPlan { spec, config })
}

/// The feature names a configuration token names, or `None` if the token is not the canonical form of its features. `default` ([`DEFAULT_CONFIG`]) names none. Any other token is feature names joined by `+` in strictly ascending order with no empty part, which is what `stream::config_token` prints. So `ss05+ss03` and `ss03+ss03` are rejected instead of being read as `ss03+ss05` and `ss03`, and `+ss03`, `ss03+`, and `ss03++ss05` are rejected for their empty part. Whether the names exist in the spec is checked later, by [`feature_syms`]. `config_token` prints a token and this parses one, so the rule is stated twice, and the unit tests check that the two agree.
fn config_features(token: &str) -> Option<Vec<&str>> {
    if token == DEFAULT_CONFIG {
        return Some(Vec::new());
    }
    let features: Vec<&str> = token.split('+').collect();
    if features.iter().any(|name| name.is_empty())
        || features.windows(2).any(|pair| pair[0] >= pair[1])
    {
        return None;
    }
    Some(features)
}

/// The configurations a list of tokens names, or `None` if any token is not canonical ([`config_features`]) or appears twice. Two runs of one configuration would write the same file.
fn config_requests(tokens: Vec<&str>) -> Option<Vec<ConfigRequest<'_>>> {
    let mut configs: Vec<ConfigRequest<'_>> = Vec::new();
    for token in tokens {
        if configs.iter().any(|named| named.token == token) {
            return None;
        }
        let features = config_features(token)?;
        configs.push(ConfigRequest { token, features });
    }
    Some(configs)
}

/// `--inputs=` is required: a persisted enumeration is accepted or rejected on its stamp, and a default stamp would let a table built from older runes pass as current. A `--previous=` or a `--windows=` that names a configuration `--configs=` does not is a usage error. A memo ceiling that is not a power of two of at least eight buckets is a usage error ([`MemoCeilings::new`]), and so is a trace ceiling that does not give each of the trace memo's shards at least eight ([`TraceCeiling::new`]).
fn plan_tables(rest: &[String]) -> Option<TablesPlan<'_>> {
    let flags = scan_flags(rest, TABLES_FLAGS)?;
    let [spec, outdir] = flags.positionals.as_slice() else {
        return None;
    };
    let memo_ceilings = MemoCeilings::new(
        flags
            .prospect_memo_buckets
            .unwrap_or(TABLE_MEMO_CEILINGS.prospect_buckets()),
        flags
            .candidates_memo_buckets
            .unwrap_or(TABLE_MEMO_CEILINGS.candidates_buckets()),
    )?;
    let trace_ceiling = TraceCeiling::new(
        flags
            .trace_memo_buckets
            .unwrap_or(TABLE_TRACE_CEILING.buckets()),
        TABLE_TRACE_CEILING.skip_concrete_deep() && flags.trace_memo_skip,
    )?;
    let configs = config_requests(flags.configs?)?;
    if flags
        .previous
        .iter()
        .any(|(token, _)| !configs.iter().any(|config| config.token == *token))
    {
        return None;
    }
    if flags
        .windows
        .iter()
        .any(|(token, _)| !configs.iter().any(|config| config.token == *token))
    {
        return None;
    }
    let windows = configs
        .iter()
        .map(|config| {
            flags
                .windows
                .iter()
                .find(|(token, _)| *token == config.token)
                .map(|(_, path)| *path)
        })
        .collect();
    let mut previous = Vec::with_capacity(configs.len());
    for config in &configs {
        previous.push(
            match flags
                .previous
                .iter()
                .find(|(token, _)| *token == config.token)
            {
                Some((_, list)) => row_merge::parse_parts(list)?,
                None => Vec::new(),
            },
        );
    }
    Some(TablesPlan {
        spec,
        outdir,
        configs,
        previous,
        windows,
        inputs: flags.inputs?,
        threads: flags.threads,
        default_memo_sharing: flags.default_memo_sharing,
        verdict_sharing: flags.verdict_sharing,
        scratch_beside_default: flags.scratch_beside_default.unwrap_or(0),
        memo_ceilings,
        trace_ceiling,
        snapshot_packing: SnapshotPacking::new(flags.snapshot_filter),
        snapshot_cache_blocks: flags.snapshot_cache_blocks.unwrap_or(SNAPSHOT_CACHE_BLOCKS),
        grouped_sweeps: flags.grouped_sweeps,
        simulated_prospect: flags.simulated_prospect,
        follower_prefer_slots: flags.follower_prefer_slots,
        deep_classes: flags.deep_classes,
        timings: flags.timings,
        cache_stats: flags.cache_stats,
    })
}

/// `--max-length=` is required because the caller records the depth the walk covered. `--memo-windows=` with `--memo-file=` is rejected: a walk that released its memo holds only the windows it settled since the release, so the file would be missing windows the build's settle memo needs. `--memo-file=` for a run that names more than one configuration is rejected too, since it names one walk's memo.
fn plan_replay(rest: &[String]) -> Option<ReplayPlan<'_>> {
    let flags = scan_flags(rest, REPLAY_FLAGS)?;
    let [spec, outdir] = flags.positionals.as_slice() else {
        return None;
    };
    if flags.memo_windows.is_some() && flags.memo_file.is_some() {
        return None;
    }
    let configs = config_requests(flags.configs?)?;
    if flags.memo_file.is_some() && configs.len() != 1 {
        return None;
    }
    Some(ReplayPlan {
        spec,
        outdir,
        configs,
        max_length: flags.max_length?,
        families: flags.families,
        last: flags.last,
        memo_file: flags.memo_file,
        memo_windows: flags.memo_windows,
        threads: flags.threads,
        simulated_prospect: flags.simulated_prospect,
        follower_prefer_slots: flags.follower_prefer_slots,
        timings: flags.timings,
        cache_stats: flags.cache_stats,
    })
}

/// The mode flags and `--cache-stats` are usage errors here, because the walk settles nothing, and so is `--segments=` without `--base-rows=`, since a merge counts every part.
fn plan_emitted(rest: &[String]) -> Option<EmittedPlan<'_>> {
    let flags = scan_flags(rest, EMITTED_FLAGS)?;
    if flags.cache_stats || !flags.simulated_prospect || !flags.follower_prefer_slots {
        return None;
    }
    if flags.segments.is_some() && flags.base_rows.is_none() {
        return None;
    }
    let [windows] = flags.positionals.as_slice() else {
        return None;
    };
    Some(EmittedPlan {
        windows,
        config: flags.config?,
        table: flags.table?,
        order: flags.order?,
        order_rules: flags.order_rules?,
        context: flags.context?,
        base_rows: flags.base_rows,
        segments: match flags.segments {
            Some(list) => row_merge::parse_parts(list)?,
            None => Vec::new(),
        },
        timings: flags.timings,
    })
}

fn read_index(path: &str) -> Result<SpecIndex, String> {
    let text = std::fs::read_to_string(path).map_err(|error| format!("{path}: {error}"))?;
    let spec = parse::parse_spec(&text).map_err(|error| format!("{path}: {error}"))?;
    Ok(SpecIndex::new(spec))
}

fn spec_echo(path: &str) -> Result<(), String> {
    let text = std::fs::read_to_string(path).map_err(|error| format!("{path}: {error}"))?;
    let spec = parse::parse_spec(&text).map_err(|error| format!("{path}: {error}"))?;
    let mut echoed = emit::emit_spec(&spec);
    echoed.push('\n');
    write_out(&echoed)
}

/// Resolves the feature names a command line gave against the spec. A name the spec never interned is an error, because dropping it would silently settle a different configuration.
fn feature_syms(index: &SpecIndex, spec: &str, names: &[&str]) -> Result<Vec<Sym>, String> {
    let mut features: Vec<Sym> = Vec::with_capacity(names.len());
    for name in names {
        features.push(
            index
                .sym_of(name)
                .ok_or_else(|| format!("{spec}: {name} is a feature this spec never mentions"))?,
        );
    }
    Ok(features)
}

/// An engine in the given modes with the trace memo on. Its caller, `settle-cases`, reaches many windows repeatedly, and a memo hit replays the fired records it journaled, so a hit gives the same answer as a fresh settle.
fn engine_for<'i>(
    index: &'i SpecIndex,
    features: Vec<Sym>,
    simulated_prospect: bool,
    follower_prefer_slots: bool,
) -> Engine<'i> {
    Engine::with_modes(
        index,
        features,
        EngineModes {
            simulated_prospect,
            follower_prefer_slots,
            trace_memo: true,
            ..EngineModes::default()
        },
    )
}

/// Settles the case lines of the named file, or of standard input for `-`, and writes one output line per case line.
fn settle_cases(plan: &CasesPlan<'_>) -> Result<(), String> {
    let index = read_index(plan.spec)?;
    let features = feature_syms(&index, plan.spec, &plan.features)?;
    let mut engine = engine_for(
        &index,
        features,
        plan.simulated_prospect,
        plan.follower_prefer_slots,
    );
    let text = if plan.cases == "-" {
        let mut text = String::new();
        std::io::stdin()
            .read_to_string(&mut text)
            .map_err(|error| format!("{}: {error}", plan.cases))?;
        text
    } else {
        std::fs::read_to_string(plan.cases).map_err(|error| format!("{}: {error}", plan.cases))?
    };
    let shape = if plan.settled_only {
        cases::CaseResult::SettledOnly
    } else {
        cases::CaseResult::Trace
    };
    let lines = cases::replay_cases(&mut engine, &text, shape)
        .map_err(|error| format!("{}: {error}", plan.cases))?;
    write_lines(&lines)
}

/// Builds each named configuration's tables under the output directory, at most `--threads` at once. Each configuration writes its settlement TSV, join TSV, and window enumeration, or for one with `--previous=` the rows of its enumeration that differ from those, at the path `--windows=` names for it when it names one. Its table digest goes to stdout as one JSON line, in command-line order, because the caller reports the digest instead of storing it as a file, beside the bytes its window file took, which a caller reading that file from a pipe checks what it received against.
fn build_tables(plan: &TablesPlan<'_>) -> Result<(), String> {
    let report = fanout::Report {
        timings: plan.timings,
        cache_stats: plan.cache_stats,
        heap: plan.cache_stats,
    };
    let mut clock = Timings::new(report);
    let started = Instant::now();
    let index = read_index(plan.spec)?;
    clock.record("spec_parse", started.elapsed());
    let mut resolved: Vec<fanout::Configuration<'_>> = Vec::with_capacity(plan.configs.len());
    for config in &plan.configs {
        resolved.push(fanout::Configuration {
            token: config.token,
            features: feature_syms(&index, plan.spec, &config.features)?,
        });
    }
    let modes = EnumerationModes {
        simulated_prospect: plan.simulated_prospect,
        follower_prefer_slots: plan.follower_prefer_slots,
        deep_classes: plan.deep_classes,
    };
    // Without `--threads` the run is serial, because only the caller knows what else is using memory. Any width is capped at the machine's parallelism, since a worker beyond it adds no throughput while its configuration holds a whole working set, and at the number of configurations, since a worker beyond that has nothing to do.
    let workers = plan
        .threads
        .unwrap_or(1)
        .min(fanout::available_threads())
        .min(resolved.len());
    let windows: Vec<Option<PathBuf>> = plan
        .windows
        .iter()
        .map(|named| named.map(PathBuf::from))
        .collect();
    let answers = fanout::run_configs_tables(
        &index,
        &resolved,
        modes,
        Path::new(plan.outdir),
        plan.inputs,
        workers,
        report,
        fanout::MemoSharing {
            default_memo_sharing: plan.default_memo_sharing,
            scratch_beside_default: plan.scratch_beside_default,
            verdict_sharing: plan.verdict_sharing,
            memo_ceilings: plan.memo_ceilings,
            trace_ceiling: plan.trace_ceiling,
            snapshot_packing: plan.snapshot_packing,
            snapshot_cache_blocks: plan.snapshot_cache_blocks,
            grouped_sweeps: plan.grouped_sweeps,
        },
        &plan.previous,
        &windows,
    )
    .map_err(|error| format!("{}: {error}", plan.spec))?;
    let mut lines: Vec<String> = Vec::with_capacity(answers.len());
    for (config, answer) in plan.configs.iter().zip(answers) {
        lines.push(format!(
            "{{\"config\":{},\"digest\":{},\"windows_bytes\":{}}}",
            json_string(config.token),
            json_string(&answer.digest),
            answer.windows_bytes
        ));
        clock.extend(answer.timed);
    }
    write_lines(&lines)?;
    clock.finish("tables_total");
    Ok(())
}

/// Replays each named configuration's persisted rules over every text up to `--max-length`, at most `--threads` at once, and writes one count line per configuration to stdout in command-line order. If any configuration's rules and engine disagree, the whole command fails with a message naming the configuration, the window, and the text, and nothing is written to stdout, because a partial answer would look clean to a caller that did not count the lines.
fn replay_strings(plan: &ReplayPlan<'_>) -> Result<(), String> {
    let report = fanout::Report {
        timings: plan.timings,
        cache_stats: plan.cache_stats,
        heap: plan.cache_stats,
    };
    let mut clock = Timings::new(report);
    let started = Instant::now();
    let index = read_index(plan.spec)?;
    clock.record("spec_parse", started.elapsed());
    let mut resolved: Vec<fanout::Configuration<'_>> = Vec::with_capacity(plan.configs.len());
    for config in &plan.configs {
        resolved.push(fanout::Configuration {
            token: config.token,
            features: feature_syms(&index, plan.spec, &config.features)?,
        });
    }
    let families: Option<Vec<Sym>> = match &plan.families {
        Some(names) => Some(
            names
                .iter()
                .map(|name| {
                    index
                        .sym_of(name)
                        .filter(|rune| index.is_modeled(*rune))
                        .ok_or_else(|| {
                            format!("{}: {name} is not a rune this spec models", plan.spec)
                        })
                })
                .collect::<Result<Vec<Sym>, String>>()?,
        ),
        None => None,
    };
    let last = match plan.last {
        Some(codepoint) => Some(
            ams_kernel::replay::symbol_at(&index, codepoint)
                .map_err(|error| format!("{}: {error}", plan.spec))?,
        ),
        None => None,
    };
    let modes = EnumerationModes {
        simulated_prospect: plan.simulated_prospect,
        follower_prefer_slots: plan.follower_prefer_slots,
        deep_classes: true,
    };
    let workers = plan
        .threads
        .unwrap_or(1)
        .min(fanout::available_threads())
        .min(resolved.len());
    let text_set = ams_kernel::replay::TextSet {
        max_length: plan.max_length,
        families: families.as_deref(),
        last,
        memo_windows: plan.memo_windows,
    };
    let answers = fanout::run_configs_replay(
        &index,
        &resolved,
        modes,
        Path::new(plan.outdir),
        text_set,
        workers,
        report,
        plan.memo_file.map(Path::new),
    )
    .map_err(|error| format!("{}: {error}", plan.spec))?;
    let mut lines: Vec<String> = Vec::with_capacity(answers.len());
    for (config, answer) in plan.configs.iter().zip(answers) {
        lines.push(format!(
            "{{\"config\":{},\"texts\":{},\"windows\":{},\"skipped\":{}}}",
            json_string(config.token),
            answer.report.texts,
            answer.report.windows,
            answer.report.skipped
        ));
        clock.extend(answer.timed);
    }
    write_lines(&lines)?;
    clock.finish("replay_total");
    Ok(())
}

/// Walks one configuration's rows against the shipped order, reading them from the named file or from standard input for `-`, merged with `--segments=` when `--base-rows=` is given, and writes one JSON line. A disagreement is the walk's own message, prefixed with the windows path, and so is a merge's refusal of a part.
fn replay_emitted(plan: &EmittedPlan<'_>) -> Result<(), String> {
    let mut clock = Timings::new(fanout::Report::timed(plan.timings));
    let started = Instant::now();
    let read =
        |path: &str| std::fs::read_to_string(path).map_err(|error| format!("{path}: {error}"));
    let table = artifacts::read_settlement_tsv(&read(plan.table)?)
        .map_err(|error| format!("{}: {error}", plan.table))?;
    let order = artifacts::read_settlement_tsv(&read(plan.order)?)
        .map_err(|error| format!("{}: {error}", plan.order))?;
    if order.len() != plan.order_rules {
        return Err(format!(
            "{}: the order holds {} rules where --order-rules= names {}",
            plan.order,
            order.len(),
            plan.order_rules
        ));
    }
    let context = shipped_order::read_context(&read(plan.context)?)
        .map_err(|error| format!("{}: {error}", plan.context))?;
    let mut walk = shipped_order::Walk::new(plan.config, &table, &order, &context);
    let windows: Box<dyn std::io::BufRead> = if plan.windows == "-" {
        Box::new(std::io::BufReader::with_capacity(
            row_merge::BUFFER_BYTES,
            std::io::stdin().lock(),
        ))
    } else {
        let file = std::fs::File::open(plan.windows)
            .map_err(|error| format!("{}: {error}", plan.windows))?;
        Box::new(std::io::BufReader::with_capacity(
            row_merge::BUFFER_BYTES,
            file,
        ))
    };
    let report = match plan.base_rows {
        None => walk.walk(&mut { windows }),
        Some(rows) => {
            let mut segments: Vec<(Box<dyn std::io::BufRead>, u64)> = Vec::new();
            for part in &plan.segments {
                let file = std::fs::File::open(&part.path)
                    .map_err(|error| format!("{}: {error}", part.path))?;
                segments.push((
                    Box::new(std::io::BufReader::with_capacity(
                        row_merge::BUFFER_BYTES,
                        file,
                    )),
                    part.rows,
                ));
            }
            walk.walk(&mut row_merge::MergedPayload::new(
                plan.config,
                windows,
                rows,
                segments,
            )?)
        }
    }
    .map_err(|error| format!("{}: {error}", plan.windows))?;
    write_lines(&[format!(
        "{{\"config\":{},\"rows\":{},\"checked_per_member\":{},\"open_rows\":{},\"continued\":{}}}",
        json_string(plan.config),
        report.rows,
        report.checked_per_member,
        report.open_rows,
        report.continued
    )])?;
    clock.record(
        &format!("replay_emitted[{}]", plan.config),
        started.elapsed(),
    );
    clock.finish("replay_emitted_total");
    Ok(())
}

/// A run's `--timings` and `--cache-stats` lines, buffered until the run ends.
///
/// A line written when its phase ended would order stderr by the thread schedule, so the buffer is written once, in command-line order. A run with neither flag records and writes nothing, because `kernel_exec` treats unexpected stderr on a clean exit as a failure.
struct Timings {
    wanted: bool,
    phases: bool,
    started: Instant,
    lines: Vec<String>,
}

impl Timings {
    fn new(report: fanout::Report) -> Self {
        Self {
            wanted: !report.silent(),
            phases: report.timings,
            started: Instant::now(),
            lines: Vec::new(),
        }
    }

    fn record(&mut self, label: &str, elapsed: Duration) {
        if self.phases {
            self.lines.push(fanout::timing_line(label, elapsed));
        }
    }

    fn extend(&mut self, lines: Vec<String>) {
        self.lines.extend(lines);
    }

    /// Writes the buffer to stderr, ending with the run's total when timed. A failed write is ignored, because the answer is already on stdout or in the files.
    fn finish(mut self, label: &str) {
        if !self.wanted {
            return;
        }
        let elapsed = self.started.elapsed();
        self.record(label, elapsed);
        let mut out = String::new();
        for line in &self.lines {
            out.push_str(line);
            out.push('\n');
        }
        let _ = std::io::stderr().write_all(out.as_bytes());
    }
}

/// Writes the guard verdict map, quantified over the powerset when no configuration is named, or answered under the one configuration named.
fn guard_sweep(plan: &GuardPlan<'_>) -> Result<(), String> {
    let index = read_index(plan.spec)?;
    let lines = match plan.config.as_ref() {
        None => guard::sweep(&index),
        Some(request) => {
            let features = feature_syms(&index, plan.spec, &request.features)?;
            guard::sweep_under(&index, features)
        }
    }
    .map_err(|error| format!("{}: {error}", plan.spec))?;
    write_lines(&lines)
}

fn write_lines(lines: &[String]) -> Result<(), String> {
    let mut out = String::new();
    for line in lines {
        out.push_str(line);
        out.push('\n');
    }
    write_out(&out)
}

fn write_out(text: &str) -> Result<(), String> {
    std::io::stdout()
        .write_all(text.as_bytes())
        .map_err(|error| format!("stdout: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// One parsed `settle-cases` plan's fields, owned, so a test does not have to keep the argument vector alive.
    #[derive(Debug, PartialEq, Eq)]
    struct Cased {
        positionals: Vec<String>,
        features: Vec<String>,
        simulated_prospect: bool,
        follower_prefer_slots: bool,
    }

    /// The same for `build-tables`, which has configurations, a thread count, a grain and the two diagnostics.
    #[derive(Debug, PartialEq, Eq)]
    struct Tabled {
        positionals: Vec<String>,
        configs: Vec<(String, Vec<String>)>,
        threads: Option<usize>,
        simulated_prospect: bool,
        follower_prefer_slots: bool,
        deep_classes: bool,
        timings: bool,
        cache_stats: bool,
    }

    fn owned(words: &[&str]) -> Vec<String> {
        words.iter().map(|word| (*word).to_owned()).collect()
    }

    /// `plan_tables` over `words` and the stamp it requires, `--inputs=stamp`, so each test names only the flags it is about. `tests/cli.rs`'s `a_table_build_without_a_stamp_is_a_usage_error` covers the stamp itself.
    fn tabled(words: &[&str]) -> Option<Tabled> {
        let mut arguments = owned(words);
        arguments.push("--inputs=stamp".to_owned());
        let plan = plan_tables(&arguments)?;
        Some(Tabled {
            positionals: vec![plan.spec.to_owned(), plan.outdir.to_owned()],
            configs: plan
                .configs
                .iter()
                .map(|config| (config.token.to_owned(), owned(&config.features)))
                .collect(),
            threads: plan.threads,
            simulated_prospect: plan.simulated_prospect,
            follower_prefer_slots: plan.follower_prefer_slots,
            deep_classes: plan.deep_classes,
            timings: plan.timings,
            cache_stats: plan.cache_stats,
        })
    }

    fn cased(words: &[&str]) -> Option<Cased> {
        let arguments = owned(words);
        let plan = plan_cases(&arguments)?;
        Some(Cased {
            positionals: vec![plan.spec.to_owned(), plan.cases.to_owned()],
            features: owned(&plan.features),
            simulated_prospect: plan.simulated_prospect,
            follower_prefer_slots: plan.follower_prefer_slots,
        })
    }

    /// The same for `replay-strings`, which has a maximum length, a family list, and memo options.
    #[derive(Debug, PartialEq, Eq)]
    struct Replayed {
        positionals: Vec<String>,
        configs: Vec<String>,
        max_length: usize,
        families: Option<Vec<String>>,
        last: Option<i64>,
        memo_file: Option<String>,
        memo_windows: Option<usize>,
        threads: Option<usize>,
        simulated_prospect: bool,
        follower_prefer_slots: bool,
        timings: bool,
        cache_stats: bool,
    }

    fn replayed(words: &[&str]) -> Option<Replayed> {
        let arguments = owned(words);
        let plan = plan_replay(&arguments)?;
        Some(Replayed {
            positionals: vec![plan.spec.to_owned(), plan.outdir.to_owned()],
            configs: plan
                .configs
                .iter()
                .map(|config| config.token.to_owned())
                .collect(),
            max_length: plan.max_length,
            families: plan.families.as_deref().map(owned),
            last: plan.last,
            memo_file: plan.memo_file.map(str::to_owned),
            memo_windows: plan.memo_windows,
            threads: plan.threads,
            simulated_prospect: plan.simulated_prospect,
            follower_prefer_slots: plan.follower_prefer_slots,
            timings: plan.timings,
            cache_stats: plan.cache_stats,
        })
    }

    /// The replay takes configurations and mode flags as the fan-out does, requires a maximum length, and accepts a family list, a last symbol, a memo file, and a memo ceiling.
    #[test]
    fn a_replay_names_its_configurations_its_max_length_and_its_families() {
        let plan = replayed(&[
            "spec.json",
            "out",
            "--configs=default,ss03",
            "--max-length=5",
            "--families=qsPea,qsTea",
            "--threads=2",
            "--timings",
        ])
        .expect("a whole replay command line");
        assert_eq!(plan.positionals, ["spec.json", "out"]);
        assert_eq!(plan.configs, ["default", "ss03"]);
        assert_eq!(plan.max_length, 5);
        assert_eq!(
            plan.families,
            Some(vec!["qsPea".to_owned(), "qsTea".to_owned()])
        );
        assert_eq!(plan.threads, Some(2));
        assert!(plan.simulated_prospect && plan.follower_prefer_slots && plan.timings);
        assert_eq!(plan.memo_file, None);
        let whole = replayed(&["spec.json", "out", "--configs=default", "--max-length=4"])
            .expect("no family list walks every text");
        assert_eq!(whole.families, None);
        assert_eq!(whole.last, None, "no last symbol walks every text");
        assert_eq!(whole.memo_windows, None, "no ceiling never releases");
        for (flag, codepoint) in [
            ("--last=U+E650", 0xE650),
            ("--last=U+0020", 0x20),
            ("--last=U+1F600", 0x1F600),
        ] {
            let ending = replayed(&[
                "spec.json",
                "out",
                "--configs=default",
                "--max-length=5",
                "--families=qsPea",
                flag,
            ])
            .expect("a last symbol narrows the walk to the texts ending in it");
            assert_eq!(ending.last, Some(codepoint), "{flag}");
        }
        assert!(!whole.timings && whole.threads.is_none());
        let ceilinged = replayed(&[
            "spec.json",
            "out",
            "--configs=default",
            "--max-length=5",
            "--memo-windows=2500000",
        ])
        .expect("a memo ceiling holds each walk's memos to that many windows");
        assert_eq!(ceilinged.memo_windows, Some(2_500_000));
        assert_eq!(ceilinged.memo_file, None);
        let piped = replayed(&[
            "spec.json",
            "out",
            "--configs=default",
            "--max-length=4",
            "--memo-file=/dev/fd/7",
        ])
        .expect("a memo file names where the one walk's window memo goes");
        assert_eq!(piped.memo_file, Some("/dev/fd/7".to_owned()));
        assert_eq!(piped.memo_windows, None);
        for refused in [
            &["--memo-file="][..],
            &["--memo-file=a", "--memo-file=b"],
            &["--memo-file=a", "--memo-windows=5"],
        ] {
            let mut words = vec!["spec.json", "out", "--configs=default", "--max-length=4"];
            words.extend_from_slice(refused);
            assert!(replayed(&words).is_none(), "{refused:?} is a usage error");
        }
        assert!(
            replayed(&[
                "spec.json",
                "out",
                "--configs=default,ss03",
                "--max-length=4",
                "--memo-file=a",
            ])
            .is_none(),
            "a memo file names one walk's memo, so it takes one configuration"
        );
        let pinned = replayed(&[
            "spec.json",
            "out",
            "--configs=default",
            "--max-length=4",
            "--candidacy-prospect",
            "--follower-prefer-slots-off",
        ])
        .expect("the replay names its mode set the way the fan-out does");
        assert!(!pinned.simulated_prospect && !pinned.follower_prefer_slots);
    }

    /// The replay rejects a missing, repeated, or non-positive maximum length; an empty or repeated family list; a last symbol that is repeated or not `U+` and four to six uppercase hexadecimal digits; a memo ceiling that is not a positive count, is repeated, or is given with a memo file; a missing configuration set or output directory; and the flags it does not accept (the grain flag, the stamp, and a feature list). No other subcommand accepts the replay's own flags.
    #[test]
    fn a_replay_without_a_max_length_or_with_a_flag_it_does_not_spell_is_refused() {
        assert!(replayed(&["spec.json", "out", "--configs=default"]).is_none());
        assert!(replayed(&["spec.json", "out", "--configs=default", "--max-length=0"]).is_none());
        assert!(replayed(&["spec.json", "out", "--configs=default", "--max-length=+3"]).is_none());
        assert!(
            replayed(&[
                "spec.json",
                "out",
                "--configs=default",
                "--max-length=4",
                "--max-length=5"
            ])
            .is_none()
        );
        assert!(
            replayed(&[
                "spec.json",
                "out",
                "--configs=default",
                "--max-length=4",
                "--families="
            ])
            .is_none()
        );
        assert!(
            replayed(&[
                "spec.json",
                "out",
                "--configs=default",
                "--max-length=4",
                "--families=qsPea",
                "--families=qsTea"
            ])
            .is_none()
        );
        for last in [
            "--last=",
            "--last=E650",
            "--last=U+",
            "--last=U+e650",
            "--last=U+650",
            "--last=U+1234567",
            "--last=U+E65G",
            "--last=u+E650",
        ] {
            assert!(
                replayed(&[
                    "spec.json",
                    "out",
                    "--configs=default",
                    "--max-length=4",
                    last
                ])
                .is_none(),
                "{last} is not a code point"
            );
        }
        assert!(
            replayed(&[
                "spec.json",
                "out",
                "--configs=default",
                "--max-length=4",
                "--last=U+E650",
                "--last=U+E651"
            ])
            .is_none()
        );
        for ceiling in [
            "--memo-windows=",
            "--memo-windows=0",
            "--memo-windows=+3",
            "--memo-windows=2.5M",
        ] {
            assert!(
                replayed(&[
                    "spec.json",
                    "out",
                    "--configs=default",
                    "--max-length=4",
                    ceiling
                ])
                .is_none(),
                "{ceiling} is not a positive count"
            );
        }
        assert!(
            replayed(&[
                "spec.json",
                "out",
                "--configs=default",
                "--max-length=4",
                "--memo-windows=5",
                "--memo-windows=5"
            ])
            .is_none()
        );
        assert!(
            replayed(&[
                "spec.json",
                "out",
                "--configs=default",
                "--max-length=4",
                "--memo-windows=5",
                "--memo-file=d"
            ])
            .is_none(),
            "a released memo is not the whole memo, so a ceiling files none"
        );
        assert!(replayed(&["spec.json", "out", "--max-length=4"]).is_none());
        assert!(replayed(&["spec.json", "--configs=default", "--max-length=4"]).is_none());
        for stray in ["--deep-classes-off", "--inputs=stamp", "--features=ss03"] {
            assert!(
                replayed(&[
                    "spec.json",
                    "out",
                    "--configs=default",
                    "--max-length=4",
                    stray
                ])
                .is_none(),
                "{stray} is not a replay flag"
            );
        }
        assert!(cased(&["spec.json", "cases.txt", "--max-length=4"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default", "--families=qsPea"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default", "--memo-file=memo"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default", "--memo-windows=5"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default", "--last=U+E650"]).is_none());
        assert!(cased(&["spec.json", "cases.txt", "--memo-windows=5"]).is_none());
    }

    /// A bare invocation runs the default modes at every subcommand.
    #[test]
    fn a_bare_command_line_names_the_default_modes() {
        let plan = tabled(&["spec.json", "out", "--configs=default"])
            .expect("two positionals and a configuration are enough");
        assert_eq!(plan.positionals, ["spec.json", "out"]);
        assert!(plan.simulated_prospect && plan.follower_prefer_slots && plan.deep_classes);
        let cases = cased(&["spec.json", "cases.txt"]).expect("two positionals");
        assert!(cases.simulated_prospect && cases.follower_prefer_slots);
        assert!(cases.features.is_empty());
    }

    /// Each mode flag turns off only its own mode: the two engine mode flags together give the pinned mode set, and `--deep-classes-off` alone gives label grain with both engine modes on.
    #[test]
    fn each_mode_flag_turns_off_the_mode_it_names() {
        let pinned = tabled(&[
            "spec.json",
            "out",
            "--configs=default,ss03",
            "--candidacy-prospect",
            "--follower-prefer-slots-off",
        ])
        .expect("a table build names one mode set for every configuration");
        assert!(!pinned.simulated_prospect && !pinned.follower_prefer_slots);
        assert!(
            pinned.deep_classes,
            "the grain flag is independent of the engine mode flags, and in this mode set it does nothing"
        );
        let label_grain = tabled(&[
            "spec.json",
            "out",
            "--configs=default",
            "--deep-classes-off",
        ])
        .expect("the label-grain case of the default modes");
        assert!(label_grain.simulated_prospect && label_grain.follower_prefer_slots);
        assert!(!label_grain.deep_classes);
        let cases = cased(&["spec.json", "cases.txt", "--candidacy-prospect"])
            .expect("the case replay names its mode set the same way");
        assert!(!cases.simulated_prospect && cases.follower_prefer_slots);
    }

    /// Only `settle-cases` accepts `--settled-only`, because only a case replay returns a trace to omit.
    #[test]
    fn only_the_case_replay_spells_the_settled_only_flag() {
        let bare = owned(&["spec.json", "cases.txt"]);
        assert!(!plan_cases(&bare).expect("two positionals").settled_only);
        let settled = owned(&["spec.json", "cases.txt", "--settled-only"]);
        assert!(
            plan_cases(&settled)
                .expect("the flag is optional")
                .settled_only
        );
        assert!(tabled(&["spec.json", "out", "--configs=default", "--settled-only"]).is_none());
    }

    /// Only the table build, the subcommand that writes rows, accepts the grain flag.
    #[test]
    fn only_the_table_build_spells_the_grain_flag() {
        assert!(cased(&["spec.json", "cases.txt", "--deep-classes-off"]).is_none());
        let label_grain = tabled(&[
            "spec.json",
            "out",
            "--configs=default",
            "--deep-classes-off",
        ])
        .expect("a table build names its grain");
        assert!(!label_grain.deep_classes);
    }

    #[test]
    fn the_feature_list_is_named_once_and_never_empty() {
        let plan = cased(&["spec.json", "cases.txt", "--features=ss03,ss05"])
            .expect("a feature list parses");
        assert_eq!(plan.features, ["ss03", "ss05"]);
        assert!(cased(&["spec.json", "cases.txt", "--features="]).is_none());
        assert!(
            cased(&[
                "spec.json",
                "cases.txt",
                "--features=ss03",
                "--features=ss05"
            ])
            .is_none()
        );
    }

    /// Every subcommand rejects a wrong positional count and an unknown flag, so a usage mistake exits 2 instead of running in the wrong mode.
    #[test]
    fn a_malformed_command_line_is_refused_rather_than_guessed_at() {
        assert!(cased(&[]).is_none());
        assert!(cased(&["spec.json"]).is_none());
        assert!(cased(&["spec.json", "cases.txt", "extra.txt"]).is_none());
        assert!(cased(&["spec.json", "cases.txt", "--live-only"]).is_none());
        assert!(tabled(&["spec.json", "--configs=default"]).is_none());
        assert!(tabled(&["spec.json", "out", "extra", "--configs=default"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default", "--live-only"]).is_none());
    }

    /// A table build names its configurations by Python's tokens, and each token parses into its features.
    #[test]
    fn a_configuration_set_parses_into_the_features_its_tokens_spell() {
        let plan = tabled(&["spec.json", "out", "--configs=default,ss03,ss03+ss05"])
            .expect("three of the acceptance configurations");
        assert_eq!(plan.positionals, ["spec.json", "out"]);
        assert_eq!(
            plan.configs,
            [
                ("default".to_owned(), Vec::new()),
                ("ss03".to_owned(), vec!["ss03".to_owned()]),
                (
                    "ss03+ss05".to_owned(),
                    vec!["ss03".to_owned(), "ss05".to_owned()]
                ),
            ]
        );
        assert!(plan.simulated_prospect && plan.follower_prefer_slots && plan.deep_classes);
        assert!(plan.threads.is_none() && !plan.timings);
    }

    /// The configuration list is required, non-empty, given once, and names each configuration once; a repeated configuration would be two runs writing one file.
    #[test]
    fn a_configuration_set_is_required_and_says_each_one_once() {
        assert!(tabled(&["spec.json", "out"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs="]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default,default"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default", "--configs=ss03"]).is_none());
    }

    /// The token rule in this file matches the library's: every accepted token is what `stream::config_token` prints for its features, and every rejected token is one `config_token` would write differently or one with an empty part.
    #[test]
    fn the_configuration_token_rule_is_config_tokens_own() {
        use ams_kernel::stream;
        for token in ["default", "ss03", "ss03+ss05", "ss03+ss04+ss05"] {
            let features = config_features(token).expect("a canonical token parses");
            assert_eq!(stream::config_token(features.iter().copied()), token);
        }
        for token in ["ss05+ss03", "ss03+ss03", "+ss03", "ss03+", "ss03++ss05", ""] {
            assert!(config_features(token).is_none(), "{token:?} is refused");
            let names: Vec<&str> = token.split('+').collect();
            assert!(
                stream::config_token(names.iter().copied()) != token
                    || names.iter().any(|name| name.is_empty()),
                "{token:?} is refused for a reason config_token's spelling shows"
            );
        }
    }

    /// A token must be the canonical form of its features, so the artifact filenames, the windows head, and the caller's name for a configuration agree.
    #[test]
    fn a_token_that_is_not_its_own_canonical_spelling_is_refused() {
        assert!(tabled(&["spec.json", "out", "--configs=ss05+ss03"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=ss03+ss03"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=+ss03"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=ss03+"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=ss03++ss05"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default,,ss03"]).is_none());
    }

    /// The thread count must be a positive count of ASCII digits that fits in `usize`, given once. Zero, a sign, other characters, and an overflowing count are usage errors, and `settle-cases` does not accept the flag.
    #[test]
    fn the_thread_count_is_a_positive_count_or_a_usage_error() {
        let plan = tabled(&["spec.json", "out", "--configs=default", "--threads=4"])
            .expect("a count parses");
        assert_eq!(plan.threads, Some(4));
        assert!(tabled(&["spec.json", "out", "--configs=default", "--threads=0"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default", "--threads=-1"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default", "--threads=+3"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default", "--threads=3 "]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default", "--threads=all"]).is_none());
        assert!(tabled(&["spec.json", "out", "--configs=default", "--threads="]).is_none());
        assert!(
            tabled(&[
                "spec.json",
                "out",
                "--configs=default",
                "--threads=99999999999999999999999999"
            ])
            .is_none()
        );
        assert!(
            tabled(&[
                "spec.json",
                "out",
                "--configs=default",
                "--threads=2",
                "--threads=3"
            ])
            .is_none()
        );
        assert!(cased(&["spec.json", "cases.txt", "--threads=4"]).is_none());
    }

    /// No subcommand accepts both `--configs=` and `--features=`: a table build's features come from its tokens, and a case replay has no configuration set.
    #[test]
    fn the_configuration_flags_belong_to_the_configuration_sets_alone() {
        assert!(tabled(&["spec.json", "out", "--configs=default", "--features=ss03"]).is_none());
        assert!(cased(&["spec.json", "cases.txt", "--configs=default"]).is_none());
    }

    /// `--timings` is optional on the table build and a usage error on the case replay.
    #[test]
    fn the_case_replay_alone_refuses_the_timings_flag() {
        assert!(
            tabled(&["spec.json", "out", "--configs=default", "--timings"])
                .expect("a table build can be timed")
                .timings
        );
        assert!(
            !tabled(&["spec.json", "out", "--configs=default"])
                .expect("a bare table build")
                .timings
        );
        assert!(cased(&["spec.json", "cases.txt", "--timings"]).is_none());
    }

    /// `--cache-stats` is accepted by the table build and the string replay, independently of `--timings`; the case replay rejects it.
    #[test]
    fn the_table_build_and_the_replay_spell_the_cache_stats_flag() {
        let with_stats = tabled(&["spec.json", "out", "--configs=default", "--cache-stats"])
            .expect("a table build can report cache stats");
        assert!(with_stats.cache_stats && !with_stats.timings);
        assert!(
            !tabled(&["spec.json", "out", "--configs=default"])
                .expect("a bare table build")
                .cache_stats
        );
        let replay = replayed(&[
            "spec.json",
            "out",
            "--configs=default",
            "--max-length=4",
            "--cache-stats",
        ])
        .expect("a replay can report cache stats");
        assert!(replay.cache_stats && !replay.timings);
        assert!(
            !replayed(&["spec.json", "out", "--configs=default", "--max-length=4"])
                .expect("a bare replay")
                .cache_stats
        );
        assert!(cased(&["spec.json", "cases.txt", "--cache-stats"]).is_none());
    }
}
