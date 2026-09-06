//! `ams-m1-kernel` — the Rust reimplementation of the M1 settlement kernel (tracker issue #40). Today it does the ingest step, the settlement core and the whole table build: it reads an `ams-m1-spec/1` dump into the interned model, echoes that model back out in canonical form (sub-issue #42), settles single windows against it — a batch of cases at a time for every Python caller that needs a window settled, and the whole late-formation surface for the guard (sub-issue #43) — runs the whole table-build worklist fixpoint over one configuration in either candidacy world and at either deep-slot grain, writing the transitions stream `kernel_io.read_transitions` parses back (sub-issues #44 and #45), runs a whole named set of configurations that way in one process, concurrently, for the builds that want all of them at once (sub-issue #46), folds those configurations into the two tables and the window enumeration a build persists rather than emitting a stream at all, replays a build's persisted rules over the whole string universe against its own settlement to check the enumeration complete (issue #176), and answers deep-slot liveness and fiber questions one key at a time, a liveness-grain inspection verb (sub-issue #45) whose Python-side differential retired at issue #78.
//!
//! **This crate is the definition of settlement.** The ranking, the refusals, the specificity order under them, the prospect and the late-formation guard have one home, and a settlement-semantics change is written here and nowhere else. What Python still binds is the boundary rather than the answer: `rebuild/pipeline/kernel_io.py` is the binding contract for the dump — it is whatever `kernel_io.spec_json` writes, and the strictness is whatever `kernel_io.spec_of` enforces — `rebuild/pipeline/model.py` for the field sets a dump carries, and `rebuild/pipeline/table.py` for the bytes [`ams_m1_kernel::fold`] and [`ams_m1_kernel::artifacts`] write, whose readers and digests it carries; byte-identity of the persisted artifacts against a stamped baseline is what holds a fold change to one answer. `rebuild/pipeline/settle.py` keeps settlement's vocabulary and none of its semantics — the token and boundary types, `cell_label`, `is_entry_bearing`, `word_position`, and the ligature formation staged before settlement, whose verdicts come from `guard-sweep` — and every other Python consumer, the conform sweep and the witness gate and explain and probe and the review surface, settles through `settle-cases`. `doc/rebuild-design.md` §14.1 carries the design facts behind the port — chiefly that the packing, not the language, is the win, and that the standard SipHash hasher beat the finalizer-less fast hasher that a first pass reached for.
//!
//! **A change to `rebuild/pipeline/model.py` is a cross-group coordination event, and it lands on this crate too.** The Python codec is driven by `dataclasses.fields`, so a new field rides the dump with no edit there; this crate spells its field sets by hand and will therefore refuse the new dump rather than silently drop the field. The spec-echo parity test in `rebuild/test_kernel_io.py`, in the contracts lane of every `make test-rebuild`, is what catches the lag, and it catches it as a byte diff.
//!
//! Three make targets drive the crate from the repo root: `make kernel-build` compiles the release binary the harnesses run, `make kernel-check` is the crate's own gate and therefore settlement's (`cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`), and `make kernel-gate` is the name a kernel-semantics change reaches for, running `kernel-check` alone today. Beyond them, settlement's trust is `gate:conform`'s: every swept text is shaped through HarfBuzz and checked against this kernel's own per-window answers on every cycle.
//!
//! The CLI is positional arguments and a hand-rolled flag scan, never an argument parser, and stdout carries the answer and nothing else, ever. Three flags name the world a verb answers in, and all three are spelled as negations of the shipping configuration — `--candidacy-prospect`, `--vote-slots-off`, `--deep-classes-off` — so a bare invocation is what ships and every departure from it is visible in the command line:
//!
//! - `ams-m1-kernel spec-echo <spec>` writes the canonical dump plus one newline.
//! - `ams-m1-kernel settle-cases <spec> <cases> [--features=a,b,…] [--candidacy-prospect] [--vote-slots-off]` replays a plain-text `ams-m1-corpus/3` case file — one JSON case per line, which is what `kernel_exec.case_row` writes and `kernel_exec.trace_of` reads the answers of — through one engine in file order and writes one re-emitted case line per case. A window that raises a settlement error is a normal result line and never a nonzero exit.
//! - `ams-m1-kernel guard-sweep <spec> [--config=<token>]` writes the whole section 5.7 late-formation surface, one tab-separated verdict per line: quantified over the capability-unlock powerset, which is the surface the font ships, or answered by the one configuration `--config=` names — a token spelled as `--configs=` spells one, `default` included, so the no-feature configuration is nameable where an empty `--features=` could not name it — which is what the rebuild suite holds against the quantified one per configuration. The guard pins its own engine modes, so the two mode flags are a usage error here rather than a world to answer in.
//! - `ams-m1-kernel enumerate <spec> [--features=a,b,…] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off] [--timings]` runs one configuration's whole table-build fixpoint and writes the uncompressed `ams-m1-transitions/1` stream — the head line and one row per window. `--deep-classes-off` is Python's `AMS_DEEP_CLASSES=0`, the label-grain arm; in the pinned candidacy world enumeration is label-grain regardless, so the flag is accepted and does nothing there. The stream is written plain, which is what `kernel_exec.read_stream` parses back.
//! - `ams-m1-kernel enumerate-configs <spec> <outdir> --configs=a,b,… [--threads=N] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off] [--timings]` runs several configurations' fixpoints in one process and writes each one's stream to `<outdir>/transitions-<config>.ndjson`, creating the directory with its parents and overwriting what it finds. stdout stays silent, because here the answer is the files — and they mean nothing except on exit 0, since a configuration that fails exits 1 naming itself and leaves whatever the other configurations had already written behind. A run that does reach exit 0 leaves that promise glob-safe: any `transitions-*.ndjson` already in the directory naming a configuration this run was not asked about is swept before the first one is written, so the whole set a consumer finds there is the set the command line named. `--configs=` is required and spells the configurations the way Python does, `conform.ACCEPTANCE_CONFIGS`'s own tokens: `default` for no features, anything else a `+`-joined feature list whose names are checked against the spec exactly as `--features=` checks them. A token that is not the canonical spelling of the features it names — out of order, repeated, empty, or empty between two `+` — is a usage error rather than a configuration, which is what keeps the filename, the stream head's `config` and the caller's own word for it in agreement by construction. The world flags name one world for the whole invocation, as they do for one `enumerate`.
//! - `ams-m1-kernel build-tables <spec> <outdir> --configs=a,b,… --inputs=<stamp> [--threads=N] [--config-seed-off] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off] [--timings] [--cache-census]` runs the same fixpoints and then folds each one in place, writing `<outdir>/settlement-<config>.tsv`, `<outdir>/treaties-<config>.tsv` and the uncompressed `<outdir>/windows-<config>.tsv` under the fingerprint `--inputs` names, and writing one `{"config":…,"digest":…}` line per configuration to stdout in the order the command line named them. The configurations past `default` are enumerated as deltas over it: `default` runs first and alone, keeping its trace memo, and the rest then run `--threads` at a time reading that memo for every window naming none of their own unlocking runes ([`ams_m1_kernel::memo`]), which files the bytes a from-scratch enumeration files; `--config-seed-off` is that from-scratch arm, and a set without `default` runs as if it were on. The same memo crosses builds: `--memo-stamp=<text>` writes each configuration's finished memo as `<outdir>/memo-<config>.tsv` under a head carrying the configuration, the world and that stamp, and `--seed=<dir>` reads a previous build's files from there behind `--edited=a,b,…`, the runes whose content moved since, so a window naming none of them is answered as it was answered then; a file for another configuration or world is refused, a missing one is simply not read, and `--edited=` without `--seed=` is a usage error. No stream is written and none is read: the fold runs on the product the worklist still holds, so the several hundred megabytes a stream would cost to write and read back are never spent. The harness gzips the windows payload, as it gzips the stream, for the same reason. The directory is created and nothing in it is swept — a build writes into its own artifact directory beside a dozen other families. `--inputs=` is required, because a serialized enumeration is trusted or refused on the stamp it carries.
//! - `ams-m1-kernel replay-strings <spec> <outdir> --configs=a,b,… --horizon=N [--families=a,b,…] [--threads=N] [--candidacy-prospect] [--vote-slots-off] [--timings]` reads each named configuration's `<outdir>/settlement-<config>.tsv` back and walks every text of length 1 through `N` over the spec's alphabet — or, with `--families=`, only the texts naming one of those runes, a ligature being named through its components — applying the rules first-match with the settled left fed forward and holding every window's rule outcome to this engine's own settlement of it ([`ams_m1_kernel::replay`]). It is the enumeration-completeness check `run_m1` runs on every build, and one `{"config":…,"texts":…,"windows":…,"skipped":…}` line per configuration on stdout is a clean answer; a window the rules and the engine disagree on exits 1 naming the configuration, the window and the text it was reached in, as does a window the engine refuses. The horizon is required rather than defaulted, because the depth a walk proved is a claim its caller records. The grain flag is not spelled: a replay settles single windows, which have no grain to name.
//! - `ams-m1-kernel liveness-cases <spec> <keys> [--features=a,b,…] [--candidacy-prospect] [--vote-slots-off]` answers one deep-slot question per key line: `3<tab><input><tab><r1><tab><r2>` and `4<tab><input><tab><r1><tab><r2><tab><r3>` answer `live` or `dead` — the full filter verdict, chain arm and liveness arm together — and `fibers<tab><input><tab><r1><tab><r2>` answers with the context's fiber partition as compact JSON. Every name is a rune family name; a key naming anything else stops the run. Each output line is the key line, a tab, and the answer, in file order.
//!
//! Concurrency reaches exactly as far as the configuration and no further: `enumerate-configs` runs at most `--threads` configurations at once — serially when nobody said, and never wider than the machine's parallelism or the configuration count — and `build-tables` runs its delta wave at that width behind `default`. [`ams_m1_kernel::fanout`] carries both halves of why that is the whole of it — what makes the bytes a function of the plan rather than of the schedule, and why the worklist inside one configuration stays sequential. Peak memory rises roughly linearly with that width, since each configuration in flight holds its whole working set until its stream has been emitted, so `--threads` is the lever a machine with less memory than parallelism reaches for.
//!
//! `--cache-census` rides the same two verbs and writes `[c] <config> <collection> len=<n> cap=<m>` lines to stderr, one per memo, plus the elimination text the memos were holding and the process's resident size sampled either side of the memo release and past the sort. It is the instrument every memory decision about this crate is made with, because the arithmetic on a struct definition can only estimate what one censused run states — and it is a diagnostic rather than an answer, so it costs nothing when it is not asked for and never touches the stream. The lines ride the same buffered stderr `--timings` uses and are written in `--configs` order; the two flags are independent, so a census can be taken without a clock and the other way round.
//!
//! `--timings` rides `enumerate` and `enumerate-configs` and writes `[t] <label> <secs>s` lines to stderr at one decimal, `rebuild/pipeline/run_m1.py`'s spelling, which is what `rebuild/tools/cycle_timings.py` parses back out of a captured child: `spec_parse` for the read, the parse and the index, then `enumerate[<config>]` and `emit[<config>]` per configuration, then `enumerate_total`. Every line is buffered and written once the last configuration is done, in `--configs` order, so stderr reads the same at any thread count. Without the flag nothing reaches stderr on a clean exit, which is a contract of its own: the identity harness reads any stderr there as a failure.
//!
//! A usage mistake — wrong argument count, wrong verb, an unknown flag, a flag the named verb does not spell, an argument that is not valid Unicode — exits 2; a file that cannot be read, parsed, or validated, a directory that cannot be written, a case file or key file this build cannot answer, and a window that will not settle, exit 1 with a one-line complaint on stderr.

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
use ams_m1_kernel::{cases, emit, fanout, guard, parse};

const USAGE: &str = "usage: ams-m1-kernel spec-echo <spec>\n       ams-m1-kernel settle-cases <spec> <cases> [--features=a,b] [--candidacy-prospect] [--vote-slots-off]\n       ams-m1-kernel guard-sweep <spec> [--config=default|ss03+ss05]\n       ams-m1-kernel enumerate <spec> [--features=a,b] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off] [--timings] [--cache-census]\n       ams-m1-kernel enumerate-configs <spec> <outdir> --configs=default,ss03 [--threads=N] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off] [--timings] [--cache-census]\n       ams-m1-kernel build-tables <spec> <outdir> --configs=default,ss03 --inputs=<stamp> [--threads=N] [--config-seed-off] [--seed=<dir> [--edited=qsPea,qsTea]] [--memo-stamp=<text>] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off] [--timings] [--cache-census]\n       ams-m1-kernel replay-strings <spec> <outdir> --configs=default,ss03 --horizon=N [--families=qsPea,qsTea] [--threads=N] [--candidacy-prospect] [--vote-slots-off] [--timings]\n       ams-m1-kernel liveness-cases <spec> <keys> [--features=a,b] [--candidacy-prospect] [--vote-slots-off]";

/// What a command line named, before any verb has said how many positionals it wants. The three mode flags are spelled as negations because all three modes ship on, so a plain invocation is the shipping configuration.
struct Flags<'a> {
    positionals: Vec<&'a str>,
    features: Vec<&'a str>,
    configs: Option<Vec<&'a str>>,
    config: Option<&'a str>,
    inputs: Option<&'a str>,
    threads: Option<usize>,
    horizon: Option<usize>,
    families: Option<Vec<&'a str>>,
    timings: bool,
    census: bool,
    config_seed: bool,
    seed: Option<&'a str>,
    edited: Option<Vec<&'a str>>,
    memo_stamp: Option<&'a str>,
    simulated_prospect: bool,
    vote_slots: bool,
    deep_classes: bool,
}

/// Which of the optional flags a verb spells at all. Anything outside its verb's vocabulary is the unknown flag it is, so `--configs=` on `enumerate` is a usage error rather than a word quietly ignored, and `--features=` on `enumerate-configs` is one too — there the configurations name the features.
#[derive(Clone, Copy)]
struct Vocabulary {
    grain: bool,
    features: bool,
    /// The two flags of a multi-configuration run, `--configs=` and `--threads=`, which only ever arrive together.
    configs: bool,
    /// `--config=`, one configuration token in `--configs=`'s spelling, for the verb that answers a single named configuration or the powerset.
    config: bool,
    /// `--inputs=`, the fingerprint stamp a windows head carries, which only the table build writes one of.
    inputs: bool,
    /// `--timings` and `--cache-census`, the two stderr diagnostics, which the same verbs spell.
    timings: bool,
    /// `--horizon=` and `--families=`, the string replay's own two: how deep to walk, and which runes' texts to walk.
    horizon: bool,
    /// The table build's own four: `--config-seed-off` enumerates every configuration from scratch instead of reading `default`'s finished memo for the windows a configuration shares with it; `--seed=` names a previous build's memo files and `--edited=` the runes that moved since, which only arrives beside it; `--memo-stamp=` is the stamp this build writes its own memo files under, and a build handed none writes none.
    seeding: bool,
}

/// The flag sets the flag-bearing verbs spell. The two file-answering verbs share one, having the same vocabulary and no reason to drift apart.
const CASES_FLAGS: Vocabulary = Vocabulary {
    grain: false,
    features: true,
    config: false,
    configs: false,
    inputs: false,
    timings: false,
    horizon: false,
    seeding: false,
};
const ENUMERATE_FLAGS: Vocabulary = Vocabulary {
    grain: true,
    features: true,
    config: false,
    configs: false,
    inputs: false,
    timings: true,
    horizon: false,
    seeding: false,
};
const CONFIGS_FLAGS: Vocabulary = Vocabulary {
    grain: true,
    features: false,
    config: false,
    configs: true,
    inputs: false,
    timings: true,
    horizon: false,
    seeding: false,
};
const TABLES_FLAGS: Vocabulary = Vocabulary {
    grain: true,
    features: false,
    config: false,
    configs: true,
    inputs: true,
    timings: true,
    horizon: false,
    seeding: true,
};
/// The replay spells the fan-out's configuration flags and the timing diagnostic, plus its own two, and neither the grain nor the stamp: it settles single windows and writes no artifact. `--cache-census` rides in with `--timings` by the vocabulary's shape and [`plan_replay`] refuses it, since the walk keeps no memo the census could read.
const REPLAY_FLAGS: Vocabulary = Vocabulary {
    grain: false,
    features: false,
    config: false,
    configs: true,
    inputs: false,
    timings: true,
    horizon: true,
    seeding: false,
};
/// `guard-sweep` names one configuration or none; its world is pinned in `guard.rs`, so [`plan_guard`] refuses the mode flags [`scan_flags`] accepts for every other verb.
const GUARD_FLAGS: Vocabulary = Vocabulary {
    grain: false,
    features: false,
    config: true,
    configs: false,
    inputs: false,
    timings: false,
    horizon: false,
    seeding: false,
};

/// What a `settle-cases` invocation asked for.
struct CasesPlan<'a> {
    spec: &'a str,
    cases: &'a str,
    features: Vec<&'a str>,
    simulated_prospect: bool,
    vote_slots: bool,
}

/// What a `guard-sweep` invocation asked for: the spec, and the one configuration to answer for instead of the powerset, or none for the quantified surface.
struct GuardPlan<'a> {
    spec: &'a str,
    config: Option<ConfigRequest<'a>>,
}

/// What an `enumerate` invocation asked for — [`CasesPlan`]'s flag vocabulary over one positional, plus the grain, since a fixpoint is one configuration's whole answer and the configuration is named the same way.
struct EnumeratePlan<'a> {
    spec: &'a str,
    features: Vec<&'a str>,
    simulated_prospect: bool,
    vote_slots: bool,
    deep_classes: bool,
    timings: bool,
    census: bool,
}

/// What an `enumerate-configs` invocation asked for: [`EnumeratePlan`]'s world over a whole named set of configurations and a directory to write them into, with the feature list replaced by the configuration tokens that spell it.
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

/// One configuration a command line named: the token it was spelled by — which is the filename, the stream head's `config` and the label of its timing lines — and the feature names that token parses into.
struct ConfigRequest<'a> {
    token: &'a str,
    features: Vec<&'a str>,
}

/// What a `build-tables` invocation asked for: [`ConfigsPlan`]'s world and set of configurations, plus the fingerprint stamp every window enumeration it writes carries in its head, and whether the configurations past `default` read its memo.
struct TablesPlan<'a> {
    spec: &'a str,
    outdir: &'a str,
    configs: Vec<ConfigRequest<'a>>,
    inputs: &'a str,
    threads: Option<usize>,
    config_seed: bool,
    seed: Option<&'a str>,
    edited: Vec<&'a str>,
    memo_stamp: Option<&'a str>,
    simulated_prospect: bool,
    vote_slots: bool,
    deep_classes: bool,
    timings: bool,
    census: bool,
}

/// What a `replay-strings` invocation asked for: [`ConfigsPlan`]'s world and set of configurations over the directory their tables sit in, the depth to walk, and the runes whose texts alone are walked when the caller knows what moved.
struct ReplayPlan<'a> {
    spec: &'a str,
    outdir: &'a str,
    configs: Vec<ConfigRequest<'a>>,
    horizon: usize,
    families: Option<Vec<&'a str>>,
    threads: Option<usize>,
    simulated_prospect: bool,
    vote_slots: bool,
    timings: bool,
}

/// What a `liveness-cases` invocation asked for. There is no grain flag: a fiber partition is derived wherever the deep world holds, whatever grain an enumeration would then be written at.
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

/// The flag scan every verb shares, or `None` for anything the contract does not spell. [`Vocabulary`] says which optional flags this verb spells at all; one it does not takes them as the unknown flags they are.
///
/// An empty `--features=` is a usage error rather than a no-feature configuration: the harness omits the flag entirely when nothing is active, so an empty value means the two sides' flag sets have drifted and saying so is more useful than guessing. An empty `--configs=` is refused for the same reason, there being no such thing as a run over no configurations, and a `--threads=` that is not a positive count is refused rather than rounded up to one — digits and nothing else, so that `+3` is the typo it is rather than the three `usize`'s own parse would read it as, and a count too large to hold is refused in the same breath.
fn scan_flags(rest: &[String], vocabulary: Vocabulary) -> Option<Flags<'_>> {
    let mut positionals: Vec<&str> = Vec::new();
    let mut features: Option<Vec<&str>> = None;
    let mut configs: Option<Vec<&str>> = None;
    let mut config: Option<&str> = None;
    let mut inputs: Option<&str> = None;
    let mut threads: Option<usize> = None;
    let mut horizon: Option<usize> = None;
    let mut families: Option<Vec<&str>> = None;
    let mut timings = false;
    let mut census = false;
    let mut config_seed = true;
    let mut seed: Option<&str> = None;
    let mut edited: Option<Vec<&str>> = None;
    let mut memo_stamp: Option<&str> = None;
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
            && let Some(stamp) = argument.strip_prefix("--memo-stamp=")
        {
            if stamp.is_empty() || memo_stamp.is_some() {
                return None;
            }
            memo_stamp = Some(stamp);
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
        timings,
        census,
        config_seed,
        seed,
        edited,
        memo_stamp,
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

/// The no-feature configuration's token, `stream::DEFAULT_CONFIG` restated. A command line's tokens are checked before any spec is read, and `guard-sweep`'s handler reaches that check, so naming the stream module here would put the enumeration's stream writer inside the surface stamp's crate walk (`rebuild/test_review_code_closure.py`) for the sake of one literal; the unit tests hold the two spellings equal.
const DEFAULT_CONFIG_TOKEN: &str = "default";

/// The feature names one configuration token spells, or `None` for a token that is not its features' canonical spelling: `default` names none, and anything else is feature names joined by `+` in strictly ascending order with no empty stretch — exactly what `stream::config_token` prints for them, so `ss05+ss03` and `ss03+ss03` are refused rather than read as aliases of `ss03+ss05` and `ss03`, and `+ss03`, `ss03+` and `ss03++ss05` are refused on their empty stretch rather than left to a sort that would pass `+ss03` by putting its nameless feature first. Whether those feature names exist is the spec's question and is asked later, exactly where `--features=` asks it. The rule is restated here rather than read off `stream::config_token` for the reason [`DEFAULT_CONFIG_TOKEN`] gives, and the unit tests hold the two to one answer.
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

/// What a set of configuration tokens named, or `None` for a set this verb will not answer: every token in [`config_features`]'s canonical spelling, and none of them twice — two runs of one configuration would race for one filename.
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

/// What a `build-tables` command line named. The stamp is required rather than defaulted: it is what a serialized enumeration is trusted or refused on, and a build that wrote one under a stamp nobody chose would be a table the sweep would happily replay against runes that had since moved. `--edited=` without `--seed=` is a usage error, there being no memo for the edit to invalidate.
fn plan_tables(rest: &[String]) -> Option<TablesPlan<'_>> {
    let flags = scan_flags(rest, TABLES_FLAGS)?;
    let [spec, outdir] = flags.positionals.as_slice() else {
        return None;
    };
    if flags.edited.is_some() && flags.seed.is_none() {
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
        memo_stamp: flags.memo_stamp,
        simulated_prospect: flags.simulated_prospect,
        vote_slots: flags.vote_slots,
        deep_classes: flags.deep_classes,
        timings: flags.timings,
        census: flags.census,
    })
}

fn plan_liveness(rest: &[String]) -> Option<LivenessPlan<'_>> {
    let flags = scan_flags(rest, CASES_FLAGS)?;
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

/// What a `replay-strings` command line named. The horizon is required for the reason the table build's stamp is: the depth a walk proved is a claim the caller records, and a depth nobody chose would be a green about nothing in particular. `--cache-census` is refused here rather than in the vocabulary, which admits it beside `--timings`, because the walk keeps no memo the census could read.
fn plan_replay(rest: &[String]) -> Option<ReplayPlan<'_>> {
    let flags = scan_flags(rest, REPLAY_FLAGS)?;
    if flags.census {
        return None;
    }
    let [spec, outdir] = flags.positionals.as_slice() else {
        return None;
    };
    let configs = config_requests(flags.configs?)?;
    Some(ReplayPlan {
        spec,
        outdir,
        configs,
        horizon: flags.horizon?,
        families: flags.families,
        threads: flags.threads,
        simulated_prospect: flags.simulated_prospect,
        vote_slots: flags.vote_slots,
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

/// The stylistic sets a command line named, resolved against the spec that will answer for them.
///
/// A feature this spec never interned could never match an authored gate, so dropping it would answer a different configuration's question in silence. A named configuration is worth refusing over.
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

/// The engine one verb's world names, always with the trace memo on: every verb below re-reaches windows in the thousands, and a memo hit replays its journaled fired delta, so warm and cold owe the same answer.
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
    let lines = cases::replay_cases(&mut engine, &text)
        .map_err(|complaint| format!("{}: {complaint}", plan.cases))?;
    write_lines(&lines)
}

/// One configuration's whole fixpoint as the uncompressed transitions stream, in whichever of the four mode combinations the command line named and at whichever grain follows from them.
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

/// A whole named set of configurations' fixpoints, each one written as its own file under the directory the plan named, at most `--threads` of them at once. Nothing lands on stdout: the answer here is the files, and they are only meaningful on exit 0.
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
    // Absent `--threads` resolves serial, because only the caller knows what else is resident. The caps bind a count the command line named as well as that default: a worker past the machine's parallelism buys no throughput while its configuration holds a whole working set, and one past the last configuration would have nothing to claim.
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

/// A whole named set of configurations folded into their tables under the directory the plan named, at most `--threads` of them at once. Each configuration's settlement TSV, treaty TSV and window enumeration are the files it leaves; its contract digest is one JSON line on stdout, in the order the plan named its configurations, because a digest is a scalar its caller holds and reports rather than an artifact family of its own.
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

/// Every named configuration's persisted rules replayed over the string universe, at most `--threads` at a time, one count line per configuration on stdout in the order the plan named them. A configuration whose rules and engine disagree is the whole verb's refusal: its complaint names the configuration, the window and the text, and nothing reaches stdout, because a partial answer would read as a clean one to a caller that did not count the lines.
fn replay_strings(plan: &ReplayPlan<'_>) -> Result<(), String> {
    let report = fanout::Report::timed(plan.timings);
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
    };
    let answers = fanout::run_configs_replay(
        &index,
        &resolved,
        modes,
        Path::new(plan.outdir),
        universe,
        workers,
        report,
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

/// The `--timings` lines a run has to say, held until the run is over rather than written as they happen.
///
/// Buffering is what makes a concurrent run's stderr readable and comparable: a line written when its phase ended would order stderr by the schedule, so the whole set is written once, in the order the plan named its configurations. A run without the flag records nothing and writes nothing, which is not merely tidiness — the identity harness reads any stderr on a clean exit as a failure.
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

    /// The whole buffer on stderr, the run's own total last. A write that fails is not worth failing a finished run over: the answer is already on stdout or in the files.
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

/// The whole formation surface: quantified over the powerset when the plan names no configuration, answered by that one configuration when it does.
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

/// One key file answered through one engine in file order — the whole of the `liveness-cases` verb.
///
/// Everything the answers are read out of is built once and shared: one engine, one liveness probe, one filter per depth, one deriver. That is not a shortcut around a cold read but the arrangement the fixpoint itself runs in, and the memos it makes possible are what keep a full sweep affordable.
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

/// Everything a key needs answering through, held together so that one lend of the whole set answers any shape of key.
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

    /// One key line's answer: `live` or `dead` for the two filter shapes, and the context's fiber partition as compact JSON for the third.
    ///
    /// The probe is lent only where the engine's own modes make a deep world, which is exactly where the filters carry a liveness arm at all — with both flags off they are the own-rune chain census and nothing else, and lending a probe there would answer a question the enumeration never asks. The deriver, by contrast, answers whatever it is asked: a `fibers` key is only ever generated for a live letter-letter context of a deep world, and which contexts those are is the caller's knowledge.
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

/// The probe a filter is lent in this world: `Some(_)` where either issue-28 flag is on, and `None` in the pinned world, where the chain arm is the whole verdict.
fn probe_in<'l, 'i>(
    deep_world: bool,
    liveness: &'l mut ProspectLiveness<'i>,
) -> Option<&'l mut ProspectLiveness<'i>> {
    deep_world.then_some(liveness)
}

/// The two verdict spellings the `3` and `4` keys answer with.
fn verdict(live: bool) -> String {
    if live { "live" } else { "dead" }.to_owned()
}

/// The rune names a key spells, resolved against the spec that will answer for it. A name the spec never modeled is a hard error rather than a `dead` answer: the key was cut against some other spec, and answering it would compare two different questions.
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

/// One context's fiber partition as compact JSON: the boundary options, then one object per fiber carrying its members, its fourth-slot verdict and its r4 groups.
///
/// Every collection rides in the deriver's own order — boundary options in static-list order, fibers in first-member-encountered order, members as collected, r4 groups in option-pipeline order — because that order is what the class ids and the row stream are cut from, and a partition read as a set would call two different tables equal. A dead fourth spells its groups as the empty list.
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

    /// One parsed plan's facts, owned — a plan borrows from the argument vector, and a test that outlives the vector is easier to read than one that keeps it alive by hand.
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

    /// The same for `enumerate-configs`, whose configurations and thread count have no counterpart on the other verbs.
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

    /// The same for `replay-strings`, whose horizon and family list have no counterpart on the other verbs.
    #[derive(Debug, PartialEq, Eq)]
    struct Replayed {
        positionals: Vec<String>,
        configs: Vec<String>,
        horizon: usize,
        families: Option<Vec<String>>,
        threads: Option<usize>,
        simulated_prospect: bool,
        vote_slots: bool,
        timings: bool,
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
            threads: plan.threads,
            simulated_prospect: plan.simulated_prospect,
            vote_slots: plan.vote_slots,
            timings: plan.timings,
        })
    }

    /// The replay names its configurations the way the fan-out does, requires a horizon, and takes a family list that narrows the universe to the texts naming those runes.
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
        let whole = replayed(&["spec.json", "out", "--configs=default", "--horizon=4"])
            .expect("no family list walks the whole universe");
        assert_eq!(whole.families, None);
        assert!(!whole.timings && whole.threads.is_none());
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

    /// What the replay refuses: no horizon, a horizon that is not a positive count, a family list that is empty or named twice, a configuration set it lacks, and the flags it does not spell — the grain, the stamp, a feature list, and the cache census.
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
        assert!(replayed(&["spec.json", "out", "--horizon=4"]).is_none());
        assert!(replayed(&["spec.json", "--configs=default", "--horizon=4"]).is_none());
        for stray in [
            "--deep-classes-off",
            "--inputs=stamp",
            "--features=ss03",
            "--cache-census",
        ] {
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
    }

    /// A bare invocation is the shipping configuration at every verb, which is the whole point of spelling the flags as negations.
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

    /// The pinned candidacy world and the label-grain arm, which are the two exit-bar configurations beside the default one.
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

    /// The grain flag belongs to the two verbs that write rows: nothing else has a grain to name, and a verb that does not spell a flag treats it as the unknown flag it is.
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

    /// Every verb refuses the wrong positional count and the flag it does not know, which is what makes a usage mistake exit 2 rather than being answered in the wrong world.
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

    /// A fan-out names its configurations by the tokens Python names them by, and each one carries the features it spells.
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

    /// The configuration list is required, never empty, and never says one configuration twice — a repeat would be two runs racing for one filename.
    #[test]
    fn a_configuration_set_is_required_and_says_each_one_once() {
        assert!(fanned(&["spec.json", "out"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs="]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default,default"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default", "--configs=ss03"]).is_none());
    }

    /// The token rule restated in this file is the stream's own: the default token is the stream's, every token the rule accepts is what `config_token` prints for the features it parses into, and every token it refuses is one `config_token` would spell differently or one with an empty stretch.
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

    /// A token has to be the canonical spelling of the features it names, which is what keeps the filename, the stream head and the caller's own word for a configuration in agreement.
    #[test]
    fn a_token_that_is_not_its_own_canonical_spelling_is_refused() {
        assert!(fanned(&["spec.json", "out", "--configs=ss05+ss03"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=ss03+ss03"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=+ss03"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=ss03+"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=ss03++ss05"]).is_none());
        assert!(fanned(&["spec.json", "out", "--configs=default,,ss03"]).is_none());
    }

    /// The thread count caps concurrency and nothing else, so it is a positive count of ASCII digits or a usage error — never a zero that would claim no configuration at all, never a signed spelling `usize` would read straight through, and never a count too large for the machine to hold.
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

    /// The two verbs that spell `--configs=` and `--features=` are disjoint: a fan-out's features come from its tokens, and one enumeration has no set of configurations to name.
    #[test]
    fn the_configuration_flags_belong_to_the_fan_out_alone() {
        assert!(fanned(&["spec.json", "out", "--configs=default", "--features=ss03"]).is_none());
        assert!(enumerated(&["spec.json", "--configs=default"]).is_none());
        assert!(cased(&["spec.json", "cases.txt", "--configs=default"]).is_none());
    }

    /// Timing lines are opt-in on the two verbs that have phases worth naming, and unknown everywhere else — a clean exit that wrote to stderr is how the identity harness reads a failure.
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

    /// `--cache-census` rides the same two verbs and stands apart from `--timings`, because the RAM diagnostic and the phase clock are asked for separately.
    #[test]
    fn only_the_enumerating_verbs_spell_the_cache_census_flag() {
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
        assert!(cased(&["spec.json", "cases.txt", "--cache-census"]).is_none());
        assert!(livened(&["spec.json", "keys.txt", "--cache-census"]).is_none());
    }
}
