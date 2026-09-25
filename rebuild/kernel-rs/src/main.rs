//! `ams-m1-kernel` is the command-line binary of the M1 settlement kernel. It reads an `ams-m1-spec/1` dump into the interned model and serves one subcommand per task, listed below: echoing the dump, settling windows for the Python callers, sweeping the late-formation guard, building and folding the tables, checking the built tables by replay, and answering deep-slot liveness and fiber queries.
//!
//! **This crate is the definition of settlement.** The ranking, the refusals, the specificity order, the prospect, and the late-formation guard are implemented only here, so a settlement-semantics change is written here and nowhere else. Python defines the boundaries. `rebuild/pipeline/kernel_io.py` defines the dump: its format is what `kernel_io.spec_json` writes, and its strictness is what `kernel_io.spec_of` enforces. `rebuild/pipeline/model.py` defines the fields a dump carries. `rebuild/pipeline/table.py` reads the files [`ams_m1_kernel::fold`] and [`ams_m1_kernel::artifacts`] write and computes their digests, and a fold change is checked by byte identity of the persisted artifacts against a stamped baseline. `rebuild/pipeline/settle.py` keeps settlement's vocabulary and none of its logic: the token and boundary types, `cell_label`, `is_entry_bearing`, `word_position`, and ligature formation, which runs before settlement and takes its verdicts from `guard-sweep`. Every other Python consumer (the conform sweep, the witness stage, explain, probe, and the review surface) settles through `settle-cases`. `doc/rebuild-design.md` §14.1 records the measurements behind the port, including that packing the model into integer keys, not the change of language, gives the speedup, and that the hasher in [`ams_m1_kernel::hash`] is faster than SipHash on these keys only with its finalizer.
//!
//! **A change to `rebuild/pipeline/model.py` must also be made in this crate.** The Python codec is driven by `dataclasses.fields`, so a new field enters the dump with no edit there. This crate lists its field sets by hand, so it rejects a dump with an unknown field instead of dropping the field. The spec-echo parity test in `rebuild/test_kernel_io.py`, which runs on every `make test-rebuild`, fails until the crate catches up.
//!
//! Three make targets build and check the crate. `make kernel-build` compiles the release binary the Python side runs. `make kernel-check` runs `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, and `cargo test`. `make kernel-gate` is the target to run after a kernel-semantics change; it runs `kernel-check` and nothing else. Beyond the crate's own tests, gate:conform checks settlement by shaping every swept text through HarfBuzz and comparing the result with this kernel's per-window settlement.
//!
//! The CLI takes positional arguments and scans flags by hand, with no argument parser. stdout carries the answer and nothing else. The three mode flags are written as negations of the shipping configuration (`--candidacy-prospect`, `--vote-slots-off`, `--deep-classes-off`), so a bare invocation runs what ships and every departure from it shows in the command line:
//!
//! - `ams-m1-kernel spec-echo <spec>` writes the canonical dump plus one newline.
//! - `ams-m1-kernel settle-cases <spec> <cases> [--features=a,b,…] [--settled-only] [--candidacy-prospect] [--vote-slots-off]` settles a plain-text case file through one engine, in file order. Each line of the file is one tab-separated window, as `kernel_exec.case_line` writes it. Each output line is the input line, a tab, and the answer: the whole trace as JSON (`kernel_exec.trace_of` reads it), or with `--settled-only` the settled record as seven tab-separated fields (`kernel_exec._settled_of_fields` reads it). A window that raises a settlement error gets an ordinary answer line, the same `{"raise":…,"message":…}` object in either shape, and does not change the exit status.
//! - `ams-m1-kernel guard-sweep <spec> [--config=<token>]` writes the section 5.7 late-formation surface, one tab-separated verdict per line. Without `--config=`, each verdict is quantified over the powerset of capability-unlock features, which is the surface the font ships. With `--config=`, the surface is answered under that one configuration, named by a token in `--configs=` form; `default` names the no-feature configuration, which an empty `--features=` could not. The rebuild suite compares each configuration's surface with the quantified one. The guard fixes its own engine modes, so the two mode flags are a usage error here.
//! - `ams-m1-kernel enumerate <spec> [--features=a,b,…] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off] [--timings] [--cache-census]` runs one configuration's table-build fixpoint and writes the uncompressed `ams-m1-transitions/1` stream (a head line and one row per window), which `kernel_exec.read_stream` reads. `--deep-classes-off` selects label grain, like Python's `AMS_DEEP_CLASSES=0`. With both `--candidacy-prospect` and `--vote-slots-off`, enumeration is label grain anyway, so the flag is accepted and has no effect.
//! - `ams-m1-kernel enumerate-configs <spec> <outdir> --configs=a,b,… [--threads=N] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off] [--timings] [--cache-census]` runs several configurations' fixpoints in one process and writes each stream to `<outdir>/transitions-<config>.ndjson`. It creates the directory with its parents and overwrites existing streams. Before writing, it deletes every other `transitions-*.ndjson` in the directory, so after exit 0 the directory holds only the configurations the command line named. stdout stays empty. The files are valid only on exit 0: a failing configuration exits 1 with its name in the message and leaves the other configurations' files in place. `--configs=` is required and uses Python's tokens (`conform.ACCEPTANCE_CONFIGS`): `default` for no features, otherwise a `+`-joined feature list whose names are checked against the spec as `--features=` names are. A token that is not the canonical form of its features (out of order, repeated, empty, or with an empty part between two `+`) is a usage error, so the filename, the stream head's `config`, and the caller's name for the configuration always agree. The mode flags apply to every configuration in the run.
//! - `ams-m1-kernel build-tables <spec> <outdir> --configs=a,b,… --inputs=<stamp> [--threads=N] [--config-seed-off] [--seed=<dir> [--edited=a,b,…] [--moved-classes=a,b,…]] [--memo-stamp=<text>] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off] [--timings] [--cache-census]` runs the same fixpoints and folds each product in memory, writing `<outdir>/settlement-<config>.tsv`, `<outdir>/treaties-<config>.tsv`, and the uncompressed `<outdir>/windows-<config>.tsv` under the fingerprint `--inputs=` names, and one `{"config":…,"digest":…}` line per configuration to stdout in command-line order. `default` enumerates first and alone and keeps its trace memo. The other configurations then run as deltas, `--threads` at a time, heaviest first by unlocking-rune count, with `default`'s fold taking one of the worker slots. Each delta reads `default`'s memo for every window that names none of its own unlocking runes ([`ams_m1_kernel::memo`]) and writes the same bytes a from-scratch enumeration writes. `--config-seed-off` enumerates every configuration from scratch, and a set without `default` does so anyway. `--memo-stamp=<text>` writes each configuration's finished memo as `<outdir>/memo-<config>.tsv`, with a head naming the configuration, the world, and the stamp. `--seed=<dir>` reads a previous build's memo files from that directory. `--edited=` names the runes whose content changed since that build and `--moved-classes=` the predicate classes whose membership changed, and a window whose settlement read none of them reuses its earlier answer. A seed file for another configuration or world is an error, a missing one is skipped, a moved class this spec no longer declares is ignored (no valid memo entry can have read it), and `--edited=` or `--moved-classes=` without `--seed=` is a usage error. No stream is written or read, because the fold runs on the product the worklist still holds; this saves writing and reading back several hundred megabytes per configuration. `run_m1.build_tables` gzips the windows payload and the memo files, because the crate has no compressor. The directory is created if needed and nothing in it is deleted, because a build writes into its artifact directory beside other artifacts. `--inputs=` is required because a persisted enumeration is accepted or rejected on the stamp it carries.
//! - `ams-m1-kernel replay-strings <spec> <outdir> --configs=a,b,… --horizon=N [--families=a,b,…] [--memo-dir=<dir> | --memo-windows=N] [--threads=N] [--candidacy-prospect] [--vote-slots-off] [--timings] [--cache-census]` reads each configuration's `<outdir>/settlement-<config>.tsv` and walks every text of length 1 through `N` over the spec's alphabet ([`ams_m1_kernel::replay`]). With `--families=`, it walks only the texts that name one of those runes; a ligature is named through its components. It applies the rules first-match, feeding each settled left forward, and compares every window's rule outcome with this engine's settlement of that window. `run_m1` runs it after every table build as the enumeration-completeness check. A clean run writes one `{"config":…,"texts":…,"windows":…,"skipped":…}` line per configuration to stdout. `--memo-dir=<dir>` also writes each passing walk's window memo there as `replay-windows-<config>.bin`, the input to the build's settle memo ([`ams_m1_kernel::replay::Replay::write_window_memo`]). `--memo-windows=N` caps each walk's memo at `N` windows, or at one text's windows when `N` is below the horizon: before any text that could push the memo past the cap, the walk releases its memo and its engine's memos. The walk's memory then does not grow with the size of the universe, apart from the settled records and labels it keeps, and `windows` counts window settles instead of distinct windows. `--memo-windows=` with `--memo-dir=` is a usage error, because a released memo is incomplete. A window where the rules and the engine disagree, or that the engine refuses, exits 1 with a message naming the configuration, the window, and the text it was reached in. The horizon is required because the caller records the depth the walk covered. There is no grain flag, because a replay settles single windows, which have no grain.
//! - `ams-m1-kernel replay-emitted <windows> --config=<token> --table=<settlement.tsv> --order=<order.tsv> --context=<context.tsv> [--timings]` walks one configuration's window enumeration (the plain `ams-m1-windows/2` payload at `<windows>`, or standard input for `-`) against the settlement order the font ships ([`ams_m1_kernel::shipped_order`]). `--order=` holds every configuration's rules in the order the emitter ships them, written as a settlement TSV whose provenance column names the table rules each row was folded from. `--context=` holds the configuration's marker renames and deep classes, one `rename` or `class` record per line. `--table=` is the configuration's own settlement TSV, read only to name the table's rule in a disagreement. Each row is renamed through the configuration's marker renames, and the first emitted rule for its input that matches it must give the row's outcome. A clean run writes one `{"config":…,"rows":…,"expanded":…}` line to stdout, where `expanded` counts the rows tried member by member because an emitted class matched only part of their deep class. A row for which the shipped order gives a different outcome exits 1 with a message naming the configuration, the row, the emitted rule that fired, and the table's rule. No spec is read: the tables already hold the settled answers, and the walk checks that the shipped lookup reproduces them.
//! - `ams-m1-kernel liveness-cases <spec> <keys> [--features=a,b,…] [--candidacy-prospect] [--vote-slots-off]` reads one deep-slot query per line of the key file. `3<tab><input><tab><r1><tab><r2>` and `4<tab><input><tab><r1><tab><r2><tab><r3>` return `live` or `dead`, the full filter verdict (the chain check and the liveness check together). `fibers<tab><input><tab><r1><tab><r2>` returns the context's fiber partition as compact JSON. Every name must be a rune family name, and any other name stops the run. Each output line is the key line, a tab, and the answer, in file order.
//!
//! Concurrency is per configuration. `enumerate-configs`, `build-tables`, and `replay-strings` run at most `--threads` configurations at once: one when the flag is absent, and never more than the machine's available parallelism or the number of configurations. When the set includes `default` and `--config-seed-off` is not given, `build-tables` enumerates `default` alone first and then runs the other configurations at that width, with `default`'s fold in one of the worker slots. [`ams_m1_kernel::fanout`] explains why the output bytes do not depend on the schedule and why one configuration's worklist stays sequential. Peak memory grows roughly linearly with the width, because each configuration in flight holds its whole working set, so a machine with less memory than cores should pass a smaller `--threads`.
//!
//! `--cache-census` is accepted by `enumerate`, `enumerate-configs`, `build-tables`, and `replay-strings`. It writes `[c] <config> <collection> len=<n> cap=<m>` lines to stderr, one per memo or table, plus the size of the elimination text the memos hold and the process's resident size at several points. [`ams_m1_kernel::fixpoint::enumerate_censused`] lists the enumeration's sample points and [`ams_m1_kernel::replay::Replay::take_census`] what the replay reports when a walk ends, including the release count (`releases count=`). Under `--memo-windows=`, each release also adds memo sizes and resident sizes labeled `release=<k>`. Memory decisions about this crate are made from these lines, because arithmetic on struct definitions only estimates what a censused run measures. The census costs nothing when it is not requested and never changes stdout, the stream, or the replay's answer lines. Its lines go into the same buffer as `--timings` and are written in `--configs` order. The two flags are independent.
//!
//! `--timings` is accepted by `enumerate`, `enumerate-configs`, `build-tables`, `replay-strings`, and `replay-emitted`. It writes `[t] <label> <secs>s` lines to stderr at one decimal place, the format `rebuild/tools/console.py` defines and `rebuild/tools/cycle_timings.py` parses. The lines are `spec_parse` (reading, parsing, and indexing the spec) where a spec is read, then each configuration's phases (such as `enumerate[<config>]`, `emit[<config>]`, `fold[<config>]`, or `replay[<config>]`), then the run's total. A table build also reports `fold.prefixes[<config>]` and `fold.partition[<config>]` at millisecond precision before each `fold[<config>]` line. Every line is buffered and written after the last configuration finishes, in `--configs` order, so stderr is the same at any thread count. With neither diagnostic flag, nothing is written to stderr on a clean exit, because `kernel_exec` treats any stderr on a clean exit as a failure unless it asked for timings.
//!
//! A usage mistake (a wrong argument count, an unknown subcommand, an unknown flag, a flag the subcommand does not accept, or an argument that is not valid Unicode) exits 2. An input file that cannot be read, parsed, or validated, a directory that cannot be written, a case or key file this build cannot answer, and a window that will not settle exit 1 with a one-line message on stderr.

#![forbid(unsafe_code)]

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{Duration, Instant};

use ams_m1_kernel::census::{FourthSlotFilter, ThirdSlotFilter};
use ams_m1_kernel::emit::json_string;
use ams_m1_kernel::engine::{Engine, EngineModes};
use ams_m1_kernel::fiber::{ContextFibers, DeepFiberDeriver};
use ams_m1_kernel::fixpoint::{EnumerationModes, right_token_label};
use ams_m1_kernel::index::SpecIndex;
use ams_m1_kernel::liveness::ProspectLiveness;
use ams_m1_kernel::model::Sym;
use ams_m1_kernel::options::WindowOptions;
use ams_m1_kernel::stream::feature_config_token;
use ams_m1_kernel::{artifacts, cases, emit, fanout, guard, parse, shipped_order};

const USAGE: &str = "usage: ams-m1-kernel spec-echo <spec>\n       ams-m1-kernel settle-cases <spec> <cases> [--features=a,b] [--settled-only] [--candidacy-prospect] [--vote-slots-off]\n       ams-m1-kernel guard-sweep <spec> [--config=default|ss03+ss05]\n       ams-m1-kernel enumerate <spec> [--features=a,b] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off] [--timings] [--cache-census]\n       ams-m1-kernel enumerate-configs <spec> <outdir> --configs=default,ss03 [--threads=N] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off] [--timings] [--cache-census]\n       ams-m1-kernel build-tables <spec> <outdir> --configs=default,ss03 --inputs=<stamp> [--threads=N] [--config-seed-off] [--seed=<dir> [--edited=qsPea,qsTea] [--moved-classes=a,b]] [--memo-stamp=<text>] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off] [--timings] [--cache-census]\n       ams-m1-kernel replay-strings <spec> <outdir> --configs=default,ss03 --horizon=N [--families=qsPea,qsTea] [--memo-dir=<dir> | --memo-windows=N] [--threads=N] [--candidacy-prospect] [--vote-slots-off] [--timings] [--cache-census]\n       ams-m1-kernel replay-emitted <windows> --config=default --table=<settlement.tsv> --order=<order.tsv> --context=<context.tsv> [--timings]\n       ams-m1-kernel liveness-cases <spec> <keys> [--features=a,b] [--candidacy-prospect] [--vote-slots-off]";

/// The flags and positionals a command line named, before the subcommand checks its positional count. The three mode flags are written as negations because all three modes are on in the shipping configuration.
struct Flags<'a> {
    positionals: Vec<&'a str>,
    features: Vec<&'a str>,
    configs: Option<Vec<&'a str>>,
    config: Option<&'a str>,
    inputs: Option<&'a str>,
    threads: Option<usize>,
    horizon: Option<usize>,
    families: Option<Vec<&'a str>>,
    memo_dir: Option<&'a str>,
    memo_windows: Option<usize>,
    timings: bool,
    census: bool,
    config_seed: bool,
    seed: Option<&'a str>,
    edited: Option<Vec<&'a str>>,
    moved_classes: Option<Vec<&'a str>>,
    memo_stamp: Option<&'a str>,
    table: Option<&'a str>,
    order: Option<&'a str>,
    context: Option<&'a str>,
    settled_only: bool,
    simulated_prospect: bool,
    vote_slots: bool,
    deep_classes: bool,
}

/// Which optional flags a subcommand accepts. [`scan_flags`] treats any other flag as unknown, so `--configs=` on `enumerate` is a usage error, and so is `--features=` on `enumerate-configs`, where the configuration tokens name the features.
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
    /// `--timings` and `--cache-census`, the two stderr diagnostics.
    timings: bool,
    /// The string replay's `--horizon=`, `--families=`, `--memo-dir=`, and `--memo-windows=`: how deep to walk, which runes' texts to walk, where to write each walk's window memo, and the most windows a walk keeps memoized.
    horizon: bool,
    /// `--table=`, `--order=`, and `--context=`, the three files of `replay-emitted`, which requires all three and `--config=`.
    emitted: bool,
    /// The table build's memo flags. `--config-seed-off` enumerates every configuration from scratch instead of reading `default`'s finished memo for the windows a configuration shares with it. `--seed=` names a previous build's memo files, and `--edited=` (runes) and `--moved-classes=` (predicate classes) name what changed since; those two are valid only with `--seed=`. `--memo-stamp=` is the stamp this build writes its own memo files under; without it, no memo files are written.
    seeding: bool,
    /// `--settled-only`: answer with the settled record as seven tab-separated fields instead of the whole trace. Only `settle-cases` returns a trace.
    settled: bool,
}

/// The flag sets of the subcommands that take flags. `settle-cases` and `liveness-cases` share one set except for `--settled-only`, since a liveness answer has no trace.
const CASES_FLAGS: Vocabulary = Vocabulary {
    grain: false,
    features: true,
    config: false,
    configs: false,
    inputs: false,
    timings: false,
    horizon: false,
    emitted: false,
    seeding: false,
    settled: true,
};
const LIVENESS_FLAGS: Vocabulary = Vocabulary {
    settled: false,
    ..CASES_FLAGS
};
const ENUMERATE_FLAGS: Vocabulary = Vocabulary {
    grain: true,
    features: true,
    config: false,
    configs: false,
    inputs: false,
    timings: true,
    horizon: false,
    emitted: false,
    seeding: false,
    settled: false,
};
const CONFIGS_FLAGS: Vocabulary = Vocabulary {
    grain: true,
    features: false,
    config: false,
    configs: true,
    inputs: false,
    timings: true,
    horizon: false,
    emitted: false,
    seeding: false,
    settled: false,
};
const TABLES_FLAGS: Vocabulary = Vocabulary {
    grain: true,
    features: false,
    config: false,
    configs: true,
    inputs: true,
    timings: true,
    horizon: false,
    emitted: false,
    seeding: true,
    settled: false,
};
/// The string replay accepts the fan-out's configuration flags, both stderr diagnostics, and its own four. It has no grain flag, because it settles single windows, and no stamp, because the one file it writes (the window memo, under `--memo-dir=`) is a build input and not an artifact.
const REPLAY_FLAGS: Vocabulary = Vocabulary {
    grain: false,
    features: false,
    config: false,
    configs: true,
    inputs: false,
    timings: true,
    horizon: true,
    emitted: false,
    seeding: false,
    settled: false,
};
/// `guard-sweep` takes one configuration or none. Its engine modes are fixed in `guard.rs`, so [`plan_guard`] rejects the mode flags that [`scan_flags`] accepts for every subcommand.
const GUARD_FLAGS: Vocabulary = Vocabulary {
    grain: false,
    features: false,
    config: true,
    configs: false,
    inputs: false,
    timings: false,
    horizon: false,
    emitted: false,
    seeding: false,
    settled: false,
};

/// `replay-emitted` takes one configuration, its three files, and `--timings`. It settles nothing, so [`plan_emitted`] rejects the mode flags and `--cache-census`.
const EMITTED_FLAGS: Vocabulary = Vocabulary {
    grain: false,
    features: false,
    config: true,
    configs: false,
    inputs: false,
    timings: true,
    horizon: false,
    emitted: true,
    seeding: false,
    settled: false,
};

/// What a `settle-cases` command line asked for.
struct CasesPlan<'a> {
    spec: &'a str,
    cases: &'a str,
    features: Vec<&'a str>,
    settled_only: bool,
    simulated_prospect: bool,
    vote_slots: bool,
}

/// What a `guard-sweep` command line asked for. `config` is `None` for the surface quantified over the powerset.
struct GuardPlan<'a> {
    spec: &'a str,
    config: Option<ConfigRequest<'a>>,
}

/// What an `enumerate` command line asked for.
struct EnumeratePlan<'a> {
    spec: &'a str,
    features: Vec<&'a str>,
    simulated_prospect: bool,
    vote_slots: bool,
    deep_classes: bool,
    timings: bool,
    census: bool,
}

/// What an `enumerate-configs` command line asked for: [`EnumeratePlan`]'s modes over a set of configurations named by tokens, and an output directory.
struct ConfigsPlan<'a> {
    spec: &'a str,
    outdir: &'a str,
    configs: Vec<ConfigRequest<'a>>,
    threads: Option<usize>,
    simulated_prospect: bool,
    vote_slots: bool,
    deep_classes: bool,
    timings: bool,
    census: bool,
}

/// One configuration a command line named. `token` is also the configuration's part of the filename, the stream head's `config`, and the label of its timing lines; `features` is what the token parses into.
struct ConfigRequest<'a> {
    token: &'a str,
    features: Vec<&'a str>,
}

/// What a `build-tables` command line asked for: [`ConfigsPlan`]'s fields, the fingerprint stamp written into every windows head, and the memo flags.
struct TablesPlan<'a> {
    spec: &'a str,
    outdir: &'a str,
    configs: Vec<ConfigRequest<'a>>,
    inputs: &'a str,
    threads: Option<usize>,
    config_seed: bool,
    seed: Option<&'a str>,
    edited: Vec<&'a str>,
    moved_classes: Vec<&'a str>,
    memo_stamp: Option<&'a str>,
    simulated_prospect: bool,
    vote_slots: bool,
    deep_classes: bool,
    timings: bool,
    census: bool,
}

/// What a `replay-strings` command line asked for. `outdir` is the directory the tables are read from.
struct ReplayPlan<'a> {
    spec: &'a str,
    outdir: &'a str,
    configs: Vec<ConfigRequest<'a>>,
    horizon: usize,
    families: Option<Vec<&'a str>>,
    memo_dir: Option<&'a str>,
    memo_windows: Option<usize>,
    threads: Option<usize>,
    simulated_prospect: bool,
    vote_slots: bool,
    timings: bool,
    census: bool,
}

/// What a `replay-emitted` command line asked for. `windows` is `-` for standard input.
struct EmittedPlan<'a> {
    windows: &'a str,
    config: &'a str,
    table: &'a str,
    order: &'a str,
    context: &'a str,
    timings: bool,
}

/// What a `liveness-cases` command line asked for. It has no grain flag, because a fiber partition is derived in any deep world, whatever grain an enumeration would use.
struct LivenessPlan<'a> {
    spec: &'a str,
    keys: &'a str,
    features: Vec<&'a str>,
    simulated_prospect: bool,
    vote_slots: bool,
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
        "enumerate" => {
            let Some(plan) = plan_enumerate(rest) else {
                return usage();
            };
            enumerate(&plan)
        }
        "enumerate-configs" => {
            let Some(plan) = plan_configs(rest) else {
                return usage();
            };
            enumerate_configs(&plan)
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
        "liveness-cases" => {
            let Some(plan) = plan_liveness(rest) else {
                return usage();
            };
            liveness_cases(&plan)
        }
        _ => return usage(),
    };
    match outcome {
        Ok(()) => ExitCode::SUCCESS,
        Err(complaint) => {
            eprintln!("ams-m1-kernel: {complaint}");
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
/// An empty `--features=` is a usage error, not the no-feature configuration: `kernel_exec` omits the flag when no feature is active, so an empty value means the two sides disagree about the flags. An empty `--configs=` is a usage error because a run needs at least one configuration. A count (`--threads=`, `--horizon=`, `--memo-windows=`) must be ASCII digits with a positive value that fits in `usize`, so `+3`, which `usize`'s parser would accept, is rejected.
fn scan_flags(rest: &[String], vocabulary: Vocabulary) -> Option<Flags<'_>> {
    let mut positionals: Vec<&str> = Vec::new();
    let mut features: Option<Vec<&str>> = None;
    let mut configs: Option<Vec<&str>> = None;
    let mut config: Option<&str> = None;
    let mut inputs: Option<&str> = None;
    let mut threads: Option<usize> = None;
    let mut horizon: Option<usize> = None;
    let mut families: Option<Vec<&str>> = None;
    let mut memo_dir: Option<&str> = None;
    let mut memo_windows: Option<usize> = None;
    let mut timings = false;
    let mut census = false;
    let mut config_seed = true;
    let mut seed: Option<&str> = None;
    let mut edited: Option<Vec<&str>> = None;
    let mut moved_classes: Option<Vec<&str>> = None;
    let mut memo_stamp: Option<&str> = None;
    let mut table: Option<&str> = None;
    let mut order: Option<&str> = None;
    let mut context: Option<&str> = None;
    let mut settled_only = false;
    let mut simulated_prospect = true;
    let mut vote_slots = true;
    let mut deep_classes = true;
    for argument in rest {
        if argument == "--candidacy-prospect" {
            simulated_prospect = false;
        } else if argument == "--vote-slots-off" {
            vote_slots = false;
        } else if vocabulary.grain && argument == "--deep-classes-off" {
            deep_classes = false;
        } else if vocabulary.seeding && argument == "--config-seed-off" {
            config_seed = false;
        } else if vocabulary.seeding
            && let Some(dir) = argument.strip_prefix("--seed=")
        {
            if dir.is_empty() || seed.is_some() {
                return None;
            }
            seed = Some(dir);
        } else if vocabulary.seeding
            && let Some(list) = argument.strip_prefix("--edited=")
        {
            if list.is_empty() || edited.is_some() {
                return None;
            }
            edited = Some(list.split(',').collect());
        } else if vocabulary.seeding
            && let Some(list) = argument.strip_prefix("--moved-classes=")
        {
            if list.is_empty() || moved_classes.is_some() {
                return None;
            }
            moved_classes = Some(list.split(',').collect());
        } else if vocabulary.seeding
            && let Some(stamp) = argument.strip_prefix("--memo-stamp=")
        {
            if stamp.is_empty() || memo_stamp.is_some() {
                return None;
            }
            memo_stamp = Some(stamp);
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
            && let Some(path) = argument.strip_prefix("--context=")
        {
            if path.is_empty() || context.is_some() {
                return None;
            }
            context = Some(path);
        } else if vocabulary.settled && argument == "--settled-only" {
            settled_only = true;
        } else if vocabulary.timings && argument == "--timings" {
            timings = true;
        } else if vocabulary.timings && argument == "--cache-census" {
            census = true;
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
        } else if vocabulary.horizon
            && let Some(depth) = argument.strip_prefix("--horizon=")
        {
            if horizon.is_some() || !depth.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            horizon = Some(depth.parse::<usize>().ok().filter(|depth| *depth > 0)?);
        } else if vocabulary.horizon
            && let Some(list) = argument.strip_prefix("--families=")
        {
            if list.is_empty() || families.is_some() {
                return None;
            }
            families = Some(list.split(',').collect());
        } else if vocabulary.horizon
            && let Some(dir) = argument.strip_prefix("--memo-dir=")
        {
            if dir.is_empty() || memo_dir.is_some() {
                return None;
            }
            memo_dir = Some(dir);
        } else if vocabulary.horizon
            && let Some(count) = argument.strip_prefix("--memo-windows=")
        {
            if memo_windows.is_some() || !count.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            memo_windows = Some(count.parse::<usize>().ok().filter(|count| *count > 0)?);
        } else if vocabulary.emitted && argument == "-" {
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
        horizon,
        families,
        memo_dir,
        memo_windows,
        timings,
        census,
        config_seed,
        seed,
        edited,
        moved_classes,
        memo_stamp,
        table,
        order,
        context,
        settled_only,
        simulated_prospect,
        vote_slots,
        deep_classes,
    })
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
        vote_slots: flags.vote_slots,
    })
}

fn plan_guard(rest: &[String]) -> Option<GuardPlan<'_>> {
    let flags = scan_flags(rest, GUARD_FLAGS)?;
    if !flags.simulated_prospect || !flags.vote_slots {
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

fn plan_enumerate(rest: &[String]) -> Option<EnumeratePlan<'_>> {
    let flags = scan_flags(rest, ENUMERATE_FLAGS)?;
    let [spec] = flags.positionals.as_slice() else {
        return None;
    };
    Some(EnumeratePlan {
        spec,
        features: flags.features,
        simulated_prospect: flags.simulated_prospect,
        vote_slots: flags.vote_slots,
        deep_classes: flags.deep_classes,
        timings: flags.timings,
        census: flags.census,
    })
}

/// The no-feature configuration's token, a copy of `stream::DEFAULT_CONFIG`. `guard-sweep`'s handler checks configuration tokens, so importing the stream module here would put the stream writer into the review surface's crate walk (`rebuild/test_review_code_closure.py`) for one literal. The unit tests check that the two are equal.
const DEFAULT_CONFIG_TOKEN: &str = "default";

/// The feature names a configuration token names, or `None` if the token is not the canonical form of its features. `default` names none. Any other token is feature names joined by `+` in strictly ascending order with no empty part, which is what `stream::config_token` prints. So `ss05+ss03` and `ss03+ss03` are rejected instead of being read as `ss03+ss05` and `ss03`, and `+ss03`, `ss03+`, and `ss03++ss05` are rejected for their empty part. Whether the names exist in the spec is checked later, by [`feature_syms`]. The rule is restated here for the reason [`DEFAULT_CONFIG_TOKEN`] gives, and the unit tests check that it agrees with `stream::config_token`.
fn config_features(token: &str) -> Option<Vec<&str>> {
    if token == DEFAULT_CONFIG_TOKEN {
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

fn plan_configs(rest: &[String]) -> Option<ConfigsPlan<'_>> {
    let flags = scan_flags(rest, CONFIGS_FLAGS)?;
    let [spec, outdir] = flags.positionals.as_slice() else {
        return None;
    };
    let configs = config_requests(flags.configs?)?;
    Some(ConfigsPlan {
        spec,
        outdir,
        configs,
        threads: flags.threads,
        simulated_prospect: flags.simulated_prospect,
        vote_slots: flags.vote_slots,
        deep_classes: flags.deep_classes,
        timings: flags.timings,
        census: flags.census,
    })
}

/// `--inputs=` is required: a persisted enumeration is accepted or rejected on its stamp, and a default stamp would let a table built from older runes pass as current. `--edited=` or `--moved-classes=` without `--seed=` is a usage error, because without a previous memo there is nothing for them to invalidate.
fn plan_tables(rest: &[String]) -> Option<TablesPlan<'_>> {
    let flags = scan_flags(rest, TABLES_FLAGS)?;
    let [spec, outdir] = flags.positionals.as_slice() else {
        return None;
    };
    if (flags.edited.is_some() || flags.moved_classes.is_some()) && flags.seed.is_none() {
        return None;
    }
    let configs = config_requests(flags.configs?)?;
    Some(TablesPlan {
        spec,
        outdir,
        configs,
        inputs: flags.inputs?,
        threads: flags.threads,
        config_seed: flags.config_seed,
        seed: flags.seed,
        edited: flags.edited.unwrap_or_default(),
        moved_classes: flags.moved_classes.unwrap_or_default(),
        memo_stamp: flags.memo_stamp,
        simulated_prospect: flags.simulated_prospect,
        vote_slots: flags.vote_slots,
        deep_classes: flags.deep_classes,
        timings: flags.timings,
        census: flags.census,
    })
}

fn plan_liveness(rest: &[String]) -> Option<LivenessPlan<'_>> {
    let flags = scan_flags(rest, LIVENESS_FLAGS)?;
    let [spec, keys] = flags.positionals.as_slice() else {
        return None;
    };
    Some(LivenessPlan {
        spec,
        keys,
        features: flags.features,
        simulated_prospect: flags.simulated_prospect,
        vote_slots: flags.vote_slots,
    })
}

/// `--horizon=` is required because the caller records the depth the walk covered. `--memo-windows=` with `--memo-dir=` is rejected: a walk that released its memo holds only the windows it settled since the release, so the file would be missing windows the build's settle memo needs.
fn plan_replay(rest: &[String]) -> Option<ReplayPlan<'_>> {
    let flags = scan_flags(rest, REPLAY_FLAGS)?;
    let [spec, outdir] = flags.positionals.as_slice() else {
        return None;
    };
    if flags.memo_windows.is_some() && flags.memo_dir.is_some() {
        return None;
    }
    let configs = config_requests(flags.configs?)?;
    Some(ReplayPlan {
        spec,
        outdir,
        configs,
        horizon: flags.horizon?,
        families: flags.families,
        memo_dir: flags.memo_dir,
        memo_windows: flags.memo_windows,
        threads: flags.threads,
        simulated_prospect: flags.simulated_prospect,
        vote_slots: flags.vote_slots,
        timings: flags.timings,
        census: flags.census,
    })
}

/// The mode flags and `--cache-census` are usage errors here, because the walk settles nothing.
fn plan_emitted(rest: &[String]) -> Option<EmittedPlan<'_>> {
    let flags = scan_flags(rest, EMITTED_FLAGS)?;
    if flags.census || !flags.simulated_prospect || !flags.vote_slots {
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
        context: flags.context?,
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

/// An engine in the given modes with the trace memo on. Its callers, `settle-cases` and `liveness-cases`, reach many windows repeatedly, and a memo hit replays the fired records it journaled, so a hit gives the same answer as a fresh settle.
fn engine_for<'i>(
    index: &'i SpecIndex,
    features: Vec<Sym>,
    simulated_prospect: bool,
    vote_slots: bool,
) -> Engine<'i> {
    Engine::with_modes(
        index,
        features,
        EngineModes {
            simulated_prospect,
            vote_slots,
            trace_memo: true,
            ..EngineModes::default()
        },
    )
}

fn settle_cases(plan: &CasesPlan<'_>) -> Result<(), String> {
    let index = read_index(plan.spec)?;
    let features = feature_syms(&index, plan.spec, &plan.features)?;
    let mut engine = engine_for(&index, features, plan.simulated_prospect, plan.vote_slots);
    let text =
        std::fs::read_to_string(plan.cases).map_err(|error| format!("{}: {error}", plan.cases))?;
    let answer = if plan.settled_only {
        cases::Answer::SettledOnly
    } else {
        cases::Answer::Trace
    };
    let lines = cases::replay_cases(&mut engine, &text, answer)
        .map_err(|complaint| format!("{}: {complaint}", plan.cases))?;
    write_lines(&lines)
}

/// Writes one configuration's fixpoint to stdout as the uncompressed transitions stream.
fn enumerate(plan: &EnumeratePlan<'_>) -> Result<(), String> {
    let report = fanout::Report {
        timings: plan.timings,
        census: plan.census,
    };
    let mut clock = Timings::new(report);
    let started = Instant::now();
    let index = read_index(plan.spec)?;
    clock.record("spec_parse", started.elapsed());
    let features = feature_syms(&index, plan.spec, &plan.features)?;
    let modes = EnumerationModes {
        simulated_prospect: plan.simulated_prospect,
        vote_slots: plan.vote_slots,
        deep_classes: plan.deep_classes,
    };
    let token = feature_config_token(&index, features.iter().copied());
    let config = fanout::Configuration {
        token: &token,
        features,
    };
    let timed = fanout::run_config(
        &index,
        &config,
        modes,
        &mut std::io::stdout().lock(),
        report,
    )
    .map_err(|failure| match failure {
        fanout::Failure::Refused(complaint) => format!("{}: {complaint}", plan.spec),
        fanout::Failure::Sink(error) => format!("stdout: {error}"),
    })?;
    clock.extend(timed);
    clock.finish("enumerate_total");
    Ok(())
}

/// Writes each named configuration's fixpoint to its own file under the output directory, at most `--threads` at once. Nothing is written to stdout; the files are the answer and are complete only on exit 0.
fn enumerate_configs(plan: &ConfigsPlan<'_>) -> Result<(), String> {
    let report = fanout::Report {
        timings: plan.timings,
        census: plan.census,
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
        vote_slots: plan.vote_slots,
        deep_classes: plan.deep_classes,
    };
    // Without `--threads` the run is serial, because only the caller knows what else is using memory. Any width is capped at the machine's parallelism, since a worker beyond it adds no throughput while its configuration holds a whole working set, and at the number of configurations, since a worker beyond that has nothing to do.
    let workers = plan
        .threads
        .unwrap_or(1)
        .min(fanout::available_threads())
        .min(resolved.len());
    for timed in fanout::run_configs(
        &index,
        &resolved,
        modes,
        Path::new(plan.outdir),
        workers,
        report,
    )
    .map_err(|complaint| format!("{}: {complaint}", plan.spec))?
    {
        clock.extend(timed);
    }
    clock.finish("enumerate_total");
    Ok(())
}

/// Builds each named configuration's tables under the output directory, at most `--threads` at once. Each configuration writes its settlement TSV, treaty TSV, and window enumeration. Its contract digest goes to stdout as one JSON line, in command-line order, because the caller reports the digest instead of storing it as a file.
fn build_tables(plan: &TablesPlan<'_>) -> Result<(), String> {
    let report = fanout::Report {
        timings: plan.timings,
        census: plan.census,
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
        vote_slots: plan.vote_slots,
        deep_classes: plan.deep_classes,
    };
    let workers = plan
        .threads
        .unwrap_or(1)
        .min(fanout::available_threads())
        .min(resolved.len());
    let edited = plan
        .edited
        .iter()
        .map(|name| {
            index
                .sym_of(name)
                .filter(|rune| index.is_modeled(*rune))
                .ok_or_else(|| format!("{}: {name} is not a rune this spec models", plan.spec))
        })
        .collect::<Result<Vec<Sym>, String>>()?;
    let moved_classes: Vec<Sym> = plan
        .moved_classes
        .iter()
        .filter_map(|name| index.sym_of(name))
        .collect();
    let answers = fanout::run_configs_tables(
        &index,
        &resolved,
        modes,
        Path::new(plan.outdir),
        plan.inputs,
        workers,
        report,
        fanout::Seeding {
            config_seed: plan.config_seed,
            seed_dir: plan.seed.map(PathBuf::from),
            edited,
            moved_classes,
            memo_stamp: plan.memo_stamp.map(str::to_owned),
        },
    )
    .map_err(|complaint| format!("{}: {complaint}", plan.spec))?;
    let mut lines: Vec<String> = Vec::with_capacity(answers.len());
    for (config, answer) in plan.configs.iter().zip(answers) {
        lines.push(format!(
            "{{\"config\":{},\"digest\":{}}}",
            json_string(config.token),
            json_string(&answer.digest)
        ));
        clock.extend(answer.timed);
    }
    write_lines(&lines)?;
    clock.finish("tables_total");
    Ok(())
}

/// Replays each named configuration's persisted rules over the string universe, at most `--threads` at once, and writes one count line per configuration to stdout in command-line order. If any configuration's rules and engine disagree, the whole command fails with a message naming the configuration, the window, and the text, and nothing is written to stdout, because a partial answer would look clean to a caller that did not count the lines.
fn replay_strings(plan: &ReplayPlan<'_>) -> Result<(), String> {
    let report = fanout::Report {
        timings: plan.timings,
        census: plan.census,
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
    let modes = EnumerationModes {
        simulated_prospect: plan.simulated_prospect,
        vote_slots: plan.vote_slots,
        deep_classes: true,
    };
    let workers = plan
        .threads
        .unwrap_or(1)
        .min(fanout::available_threads())
        .min(resolved.len());
    let universe = ams_m1_kernel::replay::Universe {
        horizon: plan.horizon,
        families: families.as_deref(),
        memo_windows: plan.memo_windows,
    };
    let answers = fanout::run_configs_replay(
        &index,
        &resolved,
        modes,
        Path::new(plan.outdir),
        universe,
        workers,
        report,
        plan.memo_dir.map(Path::new),
    )
    .map_err(|complaint| format!("{}: {complaint}", plan.spec))?;
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

/// Walks one configuration's rows against the shipped order, reading them from the named file or from standard input for `-`, and writes one JSON line. A disagreement is the walk's own message, prefixed with the windows path.
fn replay_emitted(plan: &EmittedPlan<'_>) -> Result<(), String> {
    let mut clock = Timings::new(fanout::Report::timed(plan.timings));
    let started = Instant::now();
    let read =
        |path: &str| std::fs::read_to_string(path).map_err(|error| format!("{path}: {error}"));
    let table = artifacts::read_settlement_tsv(&read(plan.table)?)
        .map_err(|complaint| format!("{}: {complaint}", plan.table))?;
    let order = artifacts::read_settlement_tsv(&read(plan.order)?)
        .map_err(|complaint| format!("{}: {complaint}", plan.order))?;
    let context = shipped_order::read_context(&read(plan.context)?)
        .map_err(|complaint| format!("{}: {complaint}", plan.context))?;
    let mut walk = shipped_order::Walk::new(plan.config, &table, &order, &context);
    let report = if plan.windows == "-" {
        walk.walk(&mut std::io::stdin().lock())
    } else {
        let file = std::fs::File::open(plan.windows)
            .map_err(|error| format!("{}: {error}", plan.windows))?;
        walk.walk(&mut std::io::BufReader::with_capacity(1 << 20, file))
    }
    .map_err(|complaint| format!("{}: {complaint}", plan.windows))?;
    write_lines(&[format!(
        "{{\"config\":{},\"rows\":{},\"expanded\":{}}}",
        json_string(plan.config),
        report.rows,
        report.expanded
    )])?;
    clock.record(
        &format!("replay_emitted[{}]", plan.config),
        started.elapsed(),
    );
    clock.finish("replay_emitted_total");
    Ok(())
}

/// A run's `--timings` and `--cache-census` lines, buffered until the run ends.
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

/// Writes the formation surface, quantified over the powerset when no configuration is named, or answered under the one configuration named.
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

/// Answers a key file through one engine, in file order.
///
/// One engine, one liveness probe, one filter per depth, and one deriver are built once and shared across keys, as in the fixpoint, so their memos carry from key to key.
fn liveness_cases(plan: &LivenessPlan<'_>) -> Result<(), String> {
    let index = read_index(plan.spec)?;
    let features = feature_syms(&index, plan.spec, &plan.features)?;
    let mut engine = engine_for(&index, features, plan.simulated_prospect, plan.vote_slots);
    let text =
        std::fs::read_to_string(plan.keys).map_err(|error| format!("{}: {error}", plan.keys))?;
    let mut scaffolding = LivenessScaffolding::new(&index)
        .map_err(|complaint| format!("{}: {complaint}", plan.spec))?;
    let mut lines: Vec<String> = Vec::new();
    for (seat, line) in text.lines().enumerate() {
        let answer = scaffolding
            .answer(&mut engine, line)
            .map_err(|complaint| format!("{}: line {}: {complaint}", plan.keys, seat + 1))?;
        lines.push(format!("{line}\t{answer}"));
    }
    write_lines(&lines)
}

/// The state every key shape is answered through, kept together so one mutable borrow covers any key.
struct LivenessScaffolding<'i> {
    options: WindowOptions<'i>,
    liveness: ProspectLiveness<'i>,
    third: ThirdSlotFilter<'i>,
    fourth: FourthSlotFilter<'i>,
    deriver: DeepFiberDeriver,
}

impl<'i> LivenessScaffolding<'i> {
    fn new(index: &'i SpecIndex) -> Result<Self, String> {
        Ok(Self {
            options: WindowOptions::new(index).map_err(|error| error.to_string())?,
            liveness: ProspectLiveness::new(index),
            third: ThirdSlotFilter::new(index),
            fourth: FourthSlotFilter::new(index),
            deriver: DeepFiberDeriver::new(),
        })
    }

    /// One key line's answer: `live` or `dead` for a `3` or `4` key, and the context's fiber partition as compact JSON for a `fibers` key.
    ///
    /// The filters get the liveness probe only in a deep world (`simulated_prospect` or `vote_slots` on), which is the only place they have a liveness check. With both modes off they are the own-rune chain census alone, and passing a probe would answer a question the enumeration never asks. The deriver accepts any `fibers` key; choosing contexts where a partition is meaningful is up to the caller.
    fn answer(&mut self, engine: &mut Engine<'i>, line: &str) -> Result<String, String> {
        let index = engine.index();
        let deep_world = engine.simulated_prospect() || engine.vote_slots();
        let fields: Vec<&str> = line.split('\t').collect();
        match fields.as_slice() {
            ["3", input, right1, right2] => {
                let [input, right1, right2] = families(index, [input, right1, right2])?;
                let live = self
                    .third
                    .matters(
                        engine,
                        probe_in(deep_world, &mut self.liveness),
                        input,
                        right1,
                        right2,
                    )
                    .map_err(|error| error.to_string())?;
                Ok(verdict(live))
            }
            ["4", input, right1, right2, right3] => {
                let [input, right1, right2, right3] =
                    families(index, [input, right1, right2, right3])?;
                let live = self
                    .fourth
                    .matters(
                        engine,
                        probe_in(deep_world, &mut self.liveness),
                        input,
                        right1,
                        right2,
                        right3,
                    )
                    .map_err(|error| error.to_string())?;
                Ok(verdict(live))
            }
            ["fibers", input, right1, right2] => {
                let [input, right1, right2] = families(index, [input, right1, right2])?;
                let context = self
                    .deriver
                    .context(
                        engine,
                        &mut self.liveness,
                        &mut self.fourth,
                        &mut self.options,
                        input,
                        right1,
                        right2,
                    )
                    .map_err(|error| error.to_string())?;
                Ok(fibers_json(index, &context))
            }
            _ => Err(format!(
                "not a liveness key — expected 3, 4 or fibers and its family names, tab-separated: {line:?}"
            )),
        }
    }
}

/// The probe to pass a filter: `Some` when `simulated_prospect` or `vote_slots` is on, and `None` when both are off, where the chain check alone decides the verdict.
fn probe_in<'l, 'i>(
    deep_world: bool,
    liveness: &'l mut ProspectLiveness<'i>,
) -> Option<&'l mut ProspectLiveness<'i>> {
    deep_world.then_some(liveness)
}

/// The answer word for a `3` or `4` key.
fn verdict(live: bool) -> String {
    if live { "live" } else { "dead" }.to_owned()
}

/// Resolves a key's rune names against the spec. A name the spec does not model is an error, not a `dead` answer, because the key was written for a different spec.
fn families<const N: usize>(index: &SpecIndex, names: [&&str; N]) -> Result<[Sym; N], String> {
    let mut out = [None; N];
    for (seat, name) in names.iter().enumerate() {
        out[seat] = Some(
            index
                .sym_of(name)
                .filter(|rune| index.is_modeled(*rune))
                .ok_or_else(|| format!("{name} is not a rune this spec models"))?,
        );
    }
    Ok(out.map(|rune| rune.expect("every seat was filled before the loop ended")))
}

/// One context's fiber partition as compact JSON: the boundary options, then one object per fiber with its members, its fourth-slot verdict, and its r4 groups.
///
/// Every list keeps the deriver's order ([`ContextFibers`] and [`ams_m1_kernel::fiber::Fiber`] describe it), because class ids and the row stream are derived from that order, and comparing partitions as sets would call two different tables equal. A dead fourth slot writes `r4_groups` as `[]`.
fn fibers_json(index: &SpecIndex, context: &ContextFibers) -> String {
    let boundaries = labels_json(index, &context.boundary_options);
    let fibers: Vec<String> = context
        .fibers
        .iter()
        .map(|fiber| {
            let groups: Vec<String> = fiber
                .r4_groups
                .iter()
                .map(|group| labels_json(index, group))
                .collect();
            format!(
                "{{\"members\":{},\"fourth_matters\":{},\"r4_groups\":[{}]}}",
                labels_json(index, &fiber.members),
                fiber.fourth_matters,
                groups.join(",")
            )
        })
        .collect();
    format!(
        "{{\"boundaries\":{boundaries},\"fibers\":[{}]}}",
        fibers.join(",")
    )
}

/// One token list as a compact JSON array of [`right_token_label`] labels.
fn labels_json(index: &SpecIndex, tokens: &[ams_m1_kernel::types::RightToken]) -> String {
    let quoted: Vec<String> = tokens
        .iter()
        .map(|token| json_string(&right_token_label(index, *token)))
        .collect();
    format!("[{}]", quoted.join(","))
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

    /// One parsed plan's fields, owned, so a test does not have to keep the argument vector alive.
    #[derive(Debug, PartialEq, Eq)]
    struct Named {
        positionals: Vec<String>,
        features: Vec<String>,
        simulated_prospect: bool,
        vote_slots: bool,
        deep_classes: bool,
        timings: bool,
        census: bool,
    }

    /// The same for `enumerate-configs`, which has configurations and a thread count.
    #[derive(Debug, PartialEq, Eq)]
    struct Fanned {
        positionals: Vec<String>,
        configs: Vec<(String, Vec<String>)>,
        threads: Option<usize>,
        simulated_prospect: bool,
        vote_slots: bool,
        deep_classes: bool,
        timings: bool,
        census: bool,
    }

    fn owned(words: &[&str]) -> Vec<String> {
        words.iter().map(|word| (*word).to_owned()).collect()
    }

    fn enumerated(words: &[&str]) -> Option<Named> {
        let arguments = owned(words);
        let plan = plan_enumerate(&arguments)?;
        Some(Named {
            positionals: vec![plan.spec.to_owned()],
            features: owned(&plan.features),
            simulated_prospect: plan.simulated_prospect,
            vote_slots: plan.vote_slots,
            deep_classes: plan.deep_classes,
            timings: plan.timings,
            census: plan.census,
        })
    }

    fn fanned(words: &[&str]) -> Option<Fanned> {
        let arguments = owned(words);
        let plan = plan_configs(&arguments)?;
        Some(Fanned {
            positionals: vec![plan.spec.to_owned(), plan.outdir.to_owned()],
            configs: plan
                .configs
                .iter()
                .map(|config| (config.token.to_owned(), owned(&config.features)))
                .collect(),
            threads: plan.threads,
            simulated_prospect: plan.simulated_prospect,
            vote_slots: plan.vote_slots,
            deep_classes: plan.deep_classes,
            timings: plan.timings,
            census: plan.census,
        })
    }

    fn cased(words: &[&str]) -> Option<Named> {
        let arguments = owned(words);
        let plan = plan_cases(&arguments)?;
        Some(Named {
            positionals: vec![plan.spec.to_owned(), plan.cases.to_owned()],
            features: owned(&plan.features),
            simulated_prospect: plan.simulated_prospect,
            vote_slots: plan.vote_slots,
            deep_classes: true,
            timings: false,
            census: false,
        })
    }

    fn livened(words: &[&str]) -> Option<Named> {
        let arguments = owned(words);
        let plan = plan_liveness(&arguments)?;
        Some(Named {
            positionals: vec![plan.spec.to_owned(), plan.keys.to_owned()],
            features: owned(&plan.features),
            simulated_prospect: plan.simulated_prospect,
            vote_slots: plan.vote_slots,
            deep_classes: true,
            timings: false,
            census: false,
        })
    }

    /// The same for `replay-strings`, which has a horizon, a family list, and memo options.
    #[derive(Debug, PartialEq, Eq)]
    struct Replayed {
        positionals: Vec<String>,
        configs: Vec<String>,
        horizon: usize,
        families: Option<Vec<String>>,
        memo_dir: Option<String>,
        memo_windows: Option<usize>,
        threads: Option<usize>,
        simulated_prospect: bool,
        vote_slots: bool,
        timings: bool,
        census: bool,
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
            horizon: plan.horizon,
            families: plan.families.as_deref().map(owned),
            memo_dir: plan.memo_dir.map(str::to_owned),
            memo_windows: plan.memo_windows,
            threads: plan.threads,
            simulated_prospect: plan.simulated_prospect,
            vote_slots: plan.vote_slots,
            timings: plan.timings,
            census: plan.census,
        })
    }

    /// The replay takes configurations and mode flags as the fan-out does, requires a horizon, and accepts a family list, a memo directory, and a memo ceiling.
    #[test]
    fn a_replay_names_its_configurations_its_horizon_and_its_families() {
        let plan = replayed(&[
            "spec.json",
            "out",
            "--configs=default,ss03",
            "--horizon=5",
            "--families=qsPea,qsTea",
            "--threads=2",
            "--timings",
        ])
        .expect("a whole replay command line");
        assert_eq!(plan.positionals, ["spec.json", "out"]);
        assert_eq!(plan.configs, ["default", "ss03"]);
        assert_eq!(plan.horizon, 5);
        assert_eq!(
            plan.families,
            Some(vec!["qsPea".to_owned(), "qsTea".to_owned()])
        );
        assert_eq!(plan.threads, Some(2));
        assert!(plan.simulated_prospect && plan.vote_slots && plan.timings);
        assert_eq!(plan.memo_dir, None);
        let whole = replayed(&["spec.json", "out", "--configs=default", "--horizon=4"])
            .expect("no family list walks the whole universe");
        assert_eq!(whole.families, None);
        assert_eq!(whole.memo_windows, None, "no ceiling never releases");
        assert!(!whole.timings && whole.threads.is_none());
        let ceilinged = replayed(&[
            "spec.json",
            "out",
            "--configs=default",
            "--horizon=5",
            "--memo-windows=2500000",
        ])
        .expect("a memo ceiling holds each walk's memos to that many windows");
        assert_eq!(ceilinged.memo_windows, Some(2_500_000));
        assert_eq!(ceilinged.memo_dir, None);
        let filed = replayed(&[
            "spec.json",
            "out",
            "--configs=default",
            "--horizon=4",
            "--memo-dir=memos",
        ])
        .expect("a memo directory files each walk's window memo");
        assert_eq!(filed.memo_dir, Some("memos".to_owned()));
        let pinned = replayed(&[
            "spec.json",
            "out",
            "--configs=default",
            "--horizon=4",
            "--candidacy-prospect",
            "--vote-slots-off",
        ])
        .expect("the replay names its world the way the fan-out does");
        assert!(!pinned.simulated_prospect && !pinned.vote_slots);
    }

    /// The replay rejects a missing, repeated, or non-positive horizon; an empty or repeated family list or memo directory; a memo ceiling that is not a positive count, is repeated, or is given with a memo directory; a missing configuration set or output directory; and the flags it does not accept (the grain flag, the stamp, and a feature list). No other subcommand accepts the replay's own flags.
    #[test]
    fn a_replay_without_a_horizon_or_with_a_flag_it_does_not_spell_is_refused() {
        assert!(replayed(&["spec.json", "out", "--configs=default"]).is_none());
        assert!(replayed(&["spec.json", "out", "--configs=default", "--horizon=0"]).is_none());
        assert!(replayed(&["spec.json", "out", "--configs=default", "--horizon=+3"]).is_none());
        assert!(
            replayed(&[
                "spec.json",
                "out",
                "--configs=default",
                "--horizon=4",
                "--horizon=5"
            ])
            .is_none()
        );
        assert!(
            replayed(&[
                "spec.json",
                "out",
                "--configs=default",
                "--horizon=4",
                "--families="
            ])
            .is_none()
        );
        assert!(
            replayed(&[
                "spec.json",
                "out",
                "--configs=default",
                "--horizon=4",
                "--families=qsPea",
                "--families=qsTea"
            ])
            .is_none()
        );
        assert!(
            replayed(&[
                "spec.json",
                "out",
                "--configs=default",
                "--horizon=4",
                "--memo-dir="
            ])
            .is_none()
        );
        assert!(
            replayed(&[
                "spec.json",
                "out",
                "--configs=default",
                "--horizon=4",
                "--memo-dir=a",
                "--memo-dir=b"
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
                    "--horizon=4",
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
                "--horizon=4",
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
                "--horizon=4",
                "--memo-windows=5",
                "--memo-dir=d"
            ])
            .is_none(),
            "a released memo is not the whole memo, so a ceiling files none"
        );
        assert!(replayed(&["spec.json", "out", "--horizon=4"]).is_none());
        assert!(replayed(&["spec.json", "--configs=default", "--horizon=4"]).is_none());
        for stray in ["--deep-classes-off", "--inputs=stamp", "--features=ss03"] {
            assert!(
                replayed(&[
                    "spec.json",
                    "out",
                    "--configs=default",
                    "--horizon=4",
                    stray
                ])
                .is_none(),
                "{stray} is not a replay flag"
            );
        }
        assert!(enumerated(&["spec.json", "--horizon=4"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default", "--families=qsPea"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default", "--memo-dir=memos"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default", "--memo-windows=5"]).is_none());
        assert!(enumerated(&["spec.json", "--memo-windows=5"]).is_none());
    }

    /// A bare invocation runs the shipping configuration at every subcommand.
    #[test]
    fn a_bare_command_line_names_the_shipping_world() {
        let plan = enumerated(&["spec.json"]).expect("one positional is enough");
        assert_eq!(plan.positionals, ["spec.json"]);
        assert!(plan.simulated_prospect && plan.vote_slots && plan.deep_classes);
        assert!(plan.features.is_empty());
        let cases = cased(&["spec.json", "cases.txt"]).expect("two positionals");
        assert!(cases.simulated_prospect && cases.vote_slots);
        let liveness = livened(&["spec.json", "keys.txt"]).expect("two positionals");
        assert_eq!(liveness.positionals, ["spec.json", "keys.txt"]);
        assert!(liveness.simulated_prospect && liveness.vote_slots);
    }

    /// Each mode flag turns off only its own mode: the two world flags together give the pinned candidacy world, and `--deep-classes-off` alone gives label grain in the deep world.
    #[test]
    fn each_mode_flag_turns_off_the_mode_it_names() {
        let pinned = enumerated(&["spec.json", "--candidacy-prospect", "--vote-slots-off"])
            .expect("the flags are optional, not required");
        assert!(!pinned.simulated_prospect && !pinned.vote_slots);
        assert!(
            pinned.deep_classes,
            "the grain flag is independent of the world flags, and in this world it does nothing"
        );
        let label_grain = enumerated(&["spec.json", "--deep-classes-off"])
            .expect("the label-grain arm of the deep world");
        assert!(label_grain.simulated_prospect && label_grain.vote_slots);
        assert!(!label_grain.deep_classes);
        let cases = cased(&["spec.json", "cases.txt", "--candidacy-prospect"])
            .expect("the case replay names its world the same way");
        assert!(!cases.simulated_prospect && cases.vote_slots);
        let liveness = livened(&["spec.json", "keys.txt", "--vote-slots-off"])
            .expect("and so does the liveness sweep");
        assert!(liveness.simulated_prospect && !liveness.vote_slots);
        let fan_out = fanned(&[
            "spec.json",
            "out",
            "--configs=default,ss03",
            "--candidacy-prospect",
            "--vote-slots-off",
        ])
        .expect("a fan-out names one world for the whole set");
        assert!(!fan_out.simulated_prospect && !fan_out.vote_slots);
    }

    /// Only `settle-cases` accepts `--settled-only`; a liveness answer has no trace to omit.
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
        assert!(livened(&["spec.json", "keys.txt", "--settled-only"]).is_none());
    }

    /// Only the subcommands that write rows accept the grain flag.
    #[test]
    fn only_the_enumerating_verbs_spell_the_grain_flag() {
        assert!(cased(&["spec.json", "cases.txt", "--deep-classes-off"]).is_none());
        assert!(livened(&["spec.json", "keys.txt", "--deep-classes-off"]).is_none());
        let label_grain = fanned(&[
            "spec.json",
            "out",
            "--configs=default",
            "--deep-classes-off",
        ])
        .expect("the fan-out names its grain the way one enumeration does");
        assert!(!label_grain.deep_classes);
    }

    #[test]
    fn the_feature_list_is_named_once_and_never_empty() {
        let plan =
            enumerated(&["spec.json", "--features=ss03,ss05"]).expect("a feature list parses");
        assert_eq!(plan.features, ["ss03", "ss05"]);
        assert!(enumerated(&["spec.json", "--features="]).is_none());
        assert!(enumerated(&["spec.json", "--features=ss03", "--features=ss05"]).is_none());
    }

    /// Every subcommand rejects a wrong positional count and an unknown flag, so a usage mistake exits 2 instead of running in the wrong mode.
    #[test]
    fn a_malformed_command_line_is_refused_rather_than_guessed_at() {
        assert!(enumerated(&[]).is_none());
        assert!(enumerated(&["spec.json", "extra.json"]).is_none());
        assert!(enumerated(&["spec.json", "--live-only"]).is_none());
        assert!(livened(&["spec.json"]).is_none());
        assert!(livened(&["spec.json", "keys.txt", "extra.txt"]).is_none());
        assert!(cased(&["spec.json"]).is_none());
        assert!(fanned(&["spec.json", "--configs=default"]).is_none());
        assert!(fanned(&["spec.json", "out", "extra", "--configs=default"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default", "--live-only"]).is_none());
    }

    /// A fan-out names its configurations by Python's tokens, and each token parses into its features.
    #[test]
    fn a_configuration_set_parses_into_the_features_its_tokens_spell() {
        let plan = fanned(&["spec.json", "out", "--configs=default,ss03,ss03+ss05"])
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
        assert!(plan.simulated_prospect && plan.vote_slots && plan.deep_classes);
        assert!(plan.threads.is_none() && !plan.timings);
    }

    /// The configuration list is required, non-empty, given once, and names each configuration once; a repeated configuration would be two runs writing one file.
    #[test]
    fn a_configuration_set_is_required_and_says_each_one_once() {
        assert!(fanned(&["spec.json", "out"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs="]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default,default"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default", "--configs=ss03"]).is_none());
    }

    /// The token rule in this file matches the stream module's: the default token is `stream::DEFAULT_CONFIG`, every accepted token is what `stream::config_token` prints for its features, and every rejected token is one `config_token` would write differently or one with an empty part.
    #[test]
    fn the_configuration_token_rule_is_the_streams_own() {
        use ams_m1_kernel::stream;
        assert_eq!(DEFAULT_CONFIG_TOKEN, stream::DEFAULT_CONFIG);
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
                "{token:?} is refused for a reason the stream's spelling shows"
            );
        }
    }

    /// A token must be the canonical form of its features, so the filename, the stream head, and the caller's name for a configuration agree.
    #[test]
    fn a_token_that_is_not_its_own_canonical_spelling_is_refused() {
        assert!(fanned(&["spec.json", "out", "--configs=ss05+ss03"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=ss03+ss03"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=+ss03"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=ss03+"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=ss03++ss05"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default,,ss03"]).is_none());
    }

    /// The thread count must be a positive count of ASCII digits that fits in `usize`, given once. Zero, a sign, other characters, and an overflowing count are usage errors, and `enumerate` does not accept the flag.
    #[test]
    fn the_thread_count_is_a_positive_count_or_a_usage_error() {
        let plan = fanned(&["spec.json", "out", "--configs=default", "--threads=4"])
            .expect("a count parses");
        assert_eq!(plan.threads, Some(4));
        assert!(fanned(&["spec.json", "out", "--configs=default", "--threads=0"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default", "--threads=-1"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default", "--threads=+3"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default", "--threads=3 "]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default", "--threads=all"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default", "--threads="]).is_none());
        assert!(
            fanned(&[
                "spec.json",
                "out",
                "--configs=default",
                "--threads=99999999999999999999999999"
            ])
            .is_none()
        );
        assert!(
            fanned(&[
                "spec.json",
                "out",
                "--configs=default",
                "--threads=2",
                "--threads=3"
            ])
            .is_none()
        );
        assert!(enumerated(&["spec.json", "--threads=4"]).is_none());
    }

    /// No subcommand accepts both `--configs=` and `--features=`: a fan-out's features come from its tokens, and a single enumeration has no configuration set.
    #[test]
    fn the_configuration_flags_belong_to_the_fan_out_alone() {
        assert!(fanned(&["spec.json", "out", "--configs=default", "--features=ss03"]).is_none());
        assert!(enumerated(&["spec.json", "--configs=default"]).is_none());
        assert!(cased(&["spec.json", "cases.txt", "--configs=default"]).is_none());
    }

    /// `--timings` is optional on the enumerating subcommands and a usage error on the two case subcommands.
    #[test]
    fn only_the_enumerating_verbs_spell_the_timings_flag() {
        assert!(
            enumerated(&["spec.json", "--timings"])
                .expect("one enumeration can be timed")
                .timings
        );
        assert!(
            fanned(&["spec.json", "out", "--configs=default", "--timings"])
                .expect("and so can a fan-out")
                .timings
        );
        assert!(
            !enumerated(&["spec.json"])
                .expect("a bare enumeration")
                .timings
        );
        assert!(cased(&["spec.json", "cases.txt", "--timings"]).is_none());
        assert!(livened(&["spec.json", "keys.txt", "--timings"]).is_none());
    }

    /// `--cache-census` is accepted by the enumerating subcommands and the string replay, independently of `--timings`; the two case subcommands reject it.
    #[test]
    fn the_enumerating_verbs_and_the_replay_spell_the_cache_census_flag() {
        let censused =
            enumerated(&["spec.json", "--cache-census"]).expect("one enumeration can be censused");
        assert!(censused.census && !censused.timings);
        assert!(
            fanned(&["spec.json", "out", "--configs=default", "--cache-census"])
                .expect("and so can a fan-out")
                .census
        );
        assert!(
            !enumerated(&["spec.json"])
                .expect("a bare enumeration")
                .census
        );
        let replay = replayed(&[
            "spec.json",
            "out",
            "--configs=default",
            "--horizon=4",
            "--cache-census",
        ])
        .expect("a replay can be censused");
        assert!(replay.census && !replay.timings);
        assert!(
            !replayed(&["spec.json", "out", "--configs=default", "--horizon=4"])
                .expect("a bare replay")
                .census
        );
        assert!(cased(&["spec.json", "cases.txt", "--cache-census"]).is_none());
        assert!(livened(&["spec.json", "keys.txt", "--cache-census"]).is_none());
    }
}
