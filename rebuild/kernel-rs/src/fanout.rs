//! Runs settlement configurations: one into a caller's sink, or a named set concurrently into a directory of files. The `enumerate` and `enumerate-configs` subcommands both serialize a configuration through [`run_config`], so a file the fan-out writes cannot differ from what `enumerate` writes to stdout for the same configuration.
//!
//! The output does not depend on the thread count. All configurations share one [`SpecIndex`], which has no mutable state. The engine, the window options, the two slot filters, the liveness probe, and the fiber deriver are built inside the fixpoint on each call, so each configuration has its own. The only data shared between configurations are read-only memo snapshots behind an [`Arc`], each read through an exclusion that says which of its windows a reader may use ([`crate::memo`]): `default`'s finished memo, and, when the seed directory has one, the previous build's `default` memo. `default`'s enumeration, including its memo file write, finishes before any delta starts. Its fold then runs in one of the wave's worker slots and does not use the memo.
//!
//! Parallelism stops at the configuration. A configuration's product depends only on its row set, not on the order the worklist visits windows, because a class-grain row is traced at its fiber's canonical representative. The fixpoint keeps its LIFO worklist order fixed anyway. A configuration's `cited_provenance` is what its one engine fired while tracing its windows. Splitting one configuration's worklist across threads would require the threads to share one engine's memo and fired set to reproduce the sequential result, which would cost what the split was meant to save.

use std::cmp::Reverse;
use std::collections::BTreeSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use crate::artifacts;
use crate::engine::EngineModes;
use crate::fixpoint::{self, EnumerationModes, Seed};
use crate::fold;
use crate::hash::HashSet;
use crate::index::SpecIndex;
use crate::memo::{
    Exclusion, MemoBase, MemoFile, MemoHead, MemoSnapshot, memo_path, read_memo, unlocking_runes,
};
use crate::model::Sym;
use crate::options::WindowOptions;
use crate::replay;
use crate::stream::{self, FixpointProduct};

/// One configuration to run: the token that names it (the file name, the stream head's `config`, and the label on its timing lines) and the features the token resolves to.
pub struct Configuration<'a> {
    pub token: &'a str,
    pub features: Vec<Sym>,
}

/// Which memos a table build may read before it settles a window itself, and which memo file it writes ([`crate::memo`]).
///
/// - `config_seed`: `default` enumerates first, alone, and every other configuration then reads `default`'s finished memo for the windows that name none of its own unlocking runes. When it is off, no configuration reads `default`'s in-process memo; `tests/cli.rs` checks that both settings write the same tables.
/// - `seed_dir`: a previous build's memo files, each read as a base for its own configuration behind an exclusion of `edited` (the runes whose content changed since that build) and `moved_classes` (the predicate classes whose membership changed). A configuration with no file there reads nothing.
/// - `memo_stamp`: the stamp this build writes its own memo files under, beside its tables. With no stamp, no memo files are written.
#[derive(Clone, Debug)]
pub struct Seeding {
    pub config_seed: bool,
    pub seed_dir: Option<PathBuf>,
    pub edited: Vec<Sym>,
    pub moved_classes: Vec<Sym>,
    pub memo_stamp: Option<String>,
}

impl Default for Seeding {
    fn default() -> Self {
        Self {
            config_seed: true,
            seed_dir: None,
            edited: Vec::new(),
            moved_classes: Vec::new(),
            memo_stamp: None,
        }
    }
}

/// A previous build's memo for one configuration, read from the seed directory and filtered through `keep`, or `None` when the directory has no file for it. A file that exists but belongs to another configuration or world is an error, because the caller named the seed directory so that it would be read.
fn load_seed(
    index: &SpecIndex,
    seeding: &Seeding,
    token: &str,
    world: &str,
    keep: impl Fn(&crate::engine::TraceKey) -> bool,
) -> Result<Option<Arc<MemoSnapshot>>, String> {
    let Some(dir) = &seeding.seed_dir else {
        return Ok(None);
    };
    let path = memo_path(dir, token);
    if !path.is_file() {
        return Ok(None);
    }
    let expected = MemoHead {
        config: token.to_owned(),
        world: world.to_owned(),
        stamp: String::new(),
    };
    read_memo(index, &path, &expected, keep).map(|memo| Some(Arc::new(memo)))
}

/// The memo file one configuration writes under this seeding, or `None` when the build has no stamp to write under.
fn memo_file(
    seeding: &Seeding,
    outdir: &Path,
    token: &str,
    world: &str,
    carried: Vec<MemoBase>,
) -> Option<MemoFile> {
    seeding.memo_stamp.as_ref().map(|stamp| MemoFile {
        path: memo_path(outdir, token),
        head: MemoHead {
            config: token.to_owned(),
            world: world.to_owned(),
            stamp: stamp.clone(),
        },
        carried,
    })
}

/// A base over a previous memo behind an exclusion, or `None` when there is no previous memo.
fn base_over(memo: Option<&Arc<MemoSnapshot>>, excluded: Exclusion) -> Option<MemoBase> {
    memo.map(|memo| MemoBase {
        memo: Arc::clone(memo),
        excluded,
    })
}

/// Why one configuration failed. The two cases are separate variants with no prefix added, because the caller decides what the message blames: `enumerate` blames the spec for a refusal and stdout for a failed write, and `enumerate-configs` names the configuration for either.
#[derive(Debug)]
pub enum Failure {
    /// The fixpoint or the emitter rejected this configuration, with that module's error message.
    Refused(String),
    /// Writing to the sink failed.
    Sink(std::io::Error),
}

/// The file name pattern of a configuration's stream. A run both writes these names and sweeps for them, so they are defined once: if the two disagreed, a run would delete its own output or leave the previous run's behind.
const STREAM_PREFIX: &str = "transitions-";
const STREAM_SUFFIX: &str = ".ndjson";

/// The file one configuration's stream is written to. The token is used unchanged as the rest of the name. The CLI rejects a non-canonical token before the run, so the file name matches the stream head's `config`.
pub fn transitions_path(outdir: &Path, token: &str) -> PathBuf {
    outdir.join(format!("{STREAM_PREFIX}{token}{STREAM_SUFFIX}"))
}

/// The cap on a caller's `--threads`: the machine's available parallelism, or 1 when it reports none. It ignores QoS: under `taskpolicy -b`, which confines the process to efficiency cores and is how `make test-slowly` runs, it was observed to return the full logical core count.
pub fn available_threads() -> usize {
    std::thread::available_parallelism().map_or(1, std::num::NonZero::get)
}

/// One phase's wall-clock time as `[t] <label> <secs>s` at one decimal. `INNER_LINE` in `rebuild/tools/console.py` is the pattern `cycle_timings.py` parses these lines with.
pub fn timing_line(label: &str, elapsed: Duration) -> String {
    format!("[t] {label} {:.1}s", elapsed.as_secs_f64())
}

/// One fold subphase's wall-clock time at millisecond precision, so a short subphase such as the prefix search does not round to zero.
fn fold_timing_line(label: &str, elapsed: Duration) -> String {
    format!("[t] {label} {:.3}s", elapsed.as_secs_f64())
}

/// What a run writes to stderr besides its output: phase timings and the `--cache-census` lines. Both are off by default. A run with neither writes nothing to stderr on a clean exit. `_forward_stderr` in rebuild/pipeline/kernel_exec.py relies on this: when timings were not requested, it fails on any stderr after a clean exit.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub timings: bool,
    pub census: bool,
}

impl Report {
    /// A report with timings as given and no census.
    pub fn timed(timings: bool) -> Self {
        Self {
            timings,
            census: false,
        }
    }

    /// Whether this run writes nothing to stderr.
    pub fn silent(self) -> bool {
        !self.timings && !self.census
    }
}

/// Runs one configuration into `sink`: its fixpoint, then its stream. When asked, it returns the census's `[c]` lines followed by `enumerate[<config>]` and `emit[<config>]` timing lines. Errors carry no prefix; [`Failure`] says why.
pub fn run_config(
    index: &SpecIndex,
    config: &Configuration<'_>,
    modes: EnumerationModes,
    sink: &mut dyn Write,
    report: Report,
) -> Result<Vec<String>, Failure> {
    let token = config.token;
    let timings = report.timings;
    let mut timed: Vec<String> = Vec::new();
    let mut census: Vec<String> = Vec::new();
    let started = Instant::now();
    let product = if report.census {
        fixpoint::enumerate_censused(index, &config.features, modes, &mut census)
    } else {
        fixpoint::enumerate_transitions(index, &config.features, modes)
    }
    .map_err(Failure::Refused)?;
    timed.append(&mut census);
    if timings {
        timed.push(timing_line(
            &format!("enumerate[{token}]"),
            started.elapsed(),
        ));
    }
    let started = Instant::now();
    stream::write_transitions(index, &product, sink).map_err(|failure| match failure {
        stream::WriteFailure::Refused(complaint) => Failure::Refused(complaint),
        stream::WriteFailure::Sink(error) => Failure::Sink(error),
    })?;
    if timings {
        timed.push(timing_line(&format!("emit[{token}]"), started.elapsed()));
    }
    Ok(timed)
}

/// Writes every configuration's stream under `outdir`, running at most `workers` at once, and returns each one's timing lines in the order the caller listed the configurations.
///
/// The directory is created with its parents, as the Python artifact writers do, and an existing file at a stream's path is overwritten. Every other `transitions-*.ndjson` in the directory is removed first, so a consumer that globs the directory after a clean exit finds only this run's configurations. [`claim_all`] describes the scheduling and failure handling.
pub fn run_configs(
    index: &SpecIndex,
    configs: &[Configuration<'_>],
    modes: EnumerationModes,
    outdir: &Path,
    workers: usize,
    report: Report,
) -> Result<Vec<Vec<String>>, String> {
    std::fs::create_dir_all(outdir).map_err(|error| format!("{}: {error}", outdir.display()))?;
    sweep_unnamed_streams(outdir, configs)?;
    claim_all(configs, workers, |config| {
        into_file(index, config, modes, outdir, report)
    })
}

/// Runs `answer` on every configuration with at most `workers` at once, and returns the results in the order the configurations were listed.
///
/// Each worker claims the next configuration from a shared counter, runs it, and claims again, so one worker walks the list in order and several share it without a plan ([`claim_all_leading`]). Each result carries its configuration's list position and is put back in list order by that position, so the caller's stdout and stderr do not depend on scheduling.
///
/// A `workers` of 0 runs one worker: the count caps concurrency, and walking the list takes at least one. The first failure stops further claims, since the output of a failed run is not usable anyway. The error returned is the one at the earliest list position among the failures that occurred.
fn claim_all<T: Send>(
    configs: &[Configuration<'_>],
    workers: usize,
    answer: impl Fn(&Configuration<'_>) -> Result<T, String> + Sync,
) -> Result<Vec<T>, String> {
    let work: Vec<(usize, &Configuration<'_>)> = configs.iter().enumerate().collect();
    claim_all_leading(&work, workers, || Ok(()), |config| answer(config))
        .map(|((), seated)| seat_answers(seated, configs.len()))
}

/// [`claim_all`]'s scheduler over any worklist, with one of the `workers` slots taken by `lead`. The other workers are spawned first. Then `lead` runs on the calling thread inside the pool's scope, and afterward that thread joins the claim loop. The pool is therefore `workers` wide including the lead, and the other workers are already claiming while the lead runs.
///
/// Each worklist item carries the position its result belongs at, and results come back as `(position, result)` pairs, so a caller can order its worklist by cost and still place every result where it was listed ([`seat_answers`] turns the pairs into a list). The first failure stops further claims. The error returned is from the failed item with the earliest carried position, which may differ from the earliest claimed.
///
/// `lead` needs no `Send` bound, and neither does its result, which is why this function exists: the table build's enumerated product holds `Rc`s and cannot cross threads, so `default`'s fold has to finish on the thread that enumerated it while the workers claim deltas. A failing lead stops further claims as a failing worker does, and its error takes precedence over any worker's.
fn claim_all_leading<W: Sync, L, T: Send>(
    work: &[(usize, W)],
    workers: usize,
    lead: impl FnOnce() -> Result<L, String>,
    answer: impl Fn(&W) -> Result<T, String> + Sync,
) -> Result<(L, Vec<(usize, T)>), String> {
    let spawned = workers.max(1) - 1;
    let next = AtomicUsize::new(0);
    let stop = AtomicBool::new(false);
    let answer = &answer;
    let (led, claimed) = std::thread::scope(|scope| {
        let handles: Vec<_> = (0..spawned)
            .map(|_| scope.spawn(|| claim_seats(work, &next, &stop, answer)))
            .collect();
        let led = lead();
        let own = if led.is_ok() {
            claim_seats(work, &next, &stop, answer)
        } else {
            stop.store(true, Ordering::Relaxed);
            Ok(Vec::new())
        };
        let mut claimed = vec![own];
        claimed.extend(handles.into_iter().map(|handle| {
            handle
                .join()
                .unwrap_or_else(|panic| std::panic::resume_unwind(panic))
        }));
        (led, claimed)
    });
    let led = led?;
    let mut seated: Vec<(usize, T)> = Vec::with_capacity(work.len());
    let mut failure: Option<(usize, String)> = None;
    for outcome in claimed {
        match outcome {
            Ok(answered) => seated.extend(answered),
            Err((seat, complaint)) => {
                if failure.as_ref().is_none_or(|(worst, _)| seat < *worst) {
                    failure = Some((seat, complaint));
                }
            }
        }
    }
    match failure {
        Some((_, complaint)) => Err(complaint),
        None => Ok((led, seated)),
    }
}

/// Turns `(position, result)` pairs into one result per position, in position order. A run that reported no failure has a result for every position, since claiming stops only on a failure or at the end of the list.
fn seat_answers<T>(answered: Vec<(usize, T)>, seats: usize) -> Vec<T> {
    let mut seated: Vec<Option<T>> = (0..seats).map(|_| None).collect();
    for (seat, one) in answered {
        seated[seat] = Some(one);
    }
    seated
        .into_iter()
        .map(|one| one.expect("a run with no failure seated every configuration"))
        .collect()
}

/// One worker's loop: claim the next item, run it, and repeat until the list is exhausted or any worker has failed. Each result is paired with its item's position. A failure sets the stop flag and is returned with its position, which [`claim_all_leading`] compares to pick the earliest.
fn claim_seats<W, T>(
    work: &[(usize, W)],
    next: &AtomicUsize,
    stop: &AtomicBool,
    answer: &(impl Fn(&W) -> Result<T, String> + Sync),
) -> Result<Vec<(usize, T)>, (usize, String)> {
    let mut mine: Vec<(usize, T)> = Vec::new();
    while !stop.load(Ordering::Relaxed) {
        let position = next.fetch_add(1, Ordering::Relaxed);
        let Some((seat, item)) = work.get(position) else {
            break;
        };
        match answer(item) {
            Ok(answered) => mine.push((*seat, answered)),
            Err(complaint) => {
                stop.store(true, Ordering::Relaxed);
                return Err((*seat, complaint));
            }
        }
    }
    Ok(mine)
}

/// One delta in the wave's worklist: the configuration and its unlocking runes ([`unlocking_runes`]), computed once when the worklist is built because both the claim order and the delta's memo exclusion read them.
struct DeltaWork<'c> {
    config: &'c Configuration<'c>,
    unlocking: HashSet<Sym>,
}

/// The wave's worklist: every configuration except the one at `default_seat`, each paired with its list position, sorted by unlocking-rune count, largest first, with list position as the tie-break.
///
/// The count estimates how long a delta traces on its own: a delta whose features unlock more runes can use less of `default`'s memo and enumerates more itself. The estimate only has to separate the memo-heavy deltas from the cheap ones. Claiming the heavy ones first ends the wave sooner when the machine runs fewer workers than there are deltas, because a heavy delta claimed last would run alone while the other workers sit idle. Claim order cannot change the output: each delta reads only `default`'s snapshot and the previous build's files, never another delta's output, and each result is placed by its list position.
fn delta_worklist<'c>(
    index: &SpecIndex,
    configs: &'c [Configuration<'c>],
    default_seat: usize,
) -> Vec<(usize, DeltaWork<'c>)> {
    let mut work: Vec<(usize, DeltaWork<'c>)> = configs
        .iter()
        .enumerate()
        .filter(|(seat, _)| *seat != default_seat)
        .map(|(seat, config)| {
            let unlocking = unlocking_runes(index, &config.features);
            (seat, DeltaWork { config, unlocking })
        })
        .collect();
    work.sort_by_key(|(seat, work)| (Reverse(work.unlocking.len()), *seat));
    work
}

/// One configuration's table build result: the digest [`artifacts::table_digest`] computes over its decision and treaty tables, and its timing lines.
#[derive(Debug)]
pub struct TableAnswer {
    pub digest: String,
    pub timed: Vec<String>,
}

/// Builds every configuration's settlement table, treaty table, window file, and digest under `outdir`, running at most `workers` at once. Each configuration reads the memos `seeding` allows and writes the memo file `seeding` asks for.
///
/// When `config_seed` is on and the set has the no-feature configuration and at least one other:
///
/// - `default` enumerates first and alone, keeps its memo, and writes its memo file inside that enumeration, before the wave.
/// - `default`'s fold then runs in one of the `workers` slots while the deltas run in the others ([`claim_all_leading`]). Each delta reads `default`'s memo behind an exclusion of its own unlocking runes. Wall-clock time is one full enumeration and its memo write, plus one wave. The memo is held once and shared. Because `default`'s memo write finishes before the wave starts, the rows the writer holds for the largest memo are never resident at the same time as a delta at its peak.
/// - Each delta writes its own memo file at its fixpoint's release point, so it holds no memo through its drain, sort, or fold.
/// - A delta also reads `default`'s previous memo file behind its unlocking runes, the edited runes, and the moved classes, and reads its own previous file only for the windows that name one of its unlocking runes, which are the windows `default`'s memo cannot answer for it.
/// - The previous `default` memo stays loaded through the wave, because its unaffected entries answer delta windows without retracing.
/// - The wave claims deltas in [`delta_worklist`] order. Each result carries its list position, so the results, digests, and `[t]` lines come back in the order the configurations were listed.
///
/// Otherwise there is no in-process memo to share, and each configuration reads only its own previous file.
///
/// Every previous memo is loaded without the keys that name edited runes, since those entries cannot answer a lookup or be carried into the written memo. The exclusions still check the remaining entries' reads for edited runes and moved classes at lookup and when writing.
///
/// Unlike [`run_configs`], this removes nothing from `outdir`: `run_m1.build_tables` writes into the build's artifact directory beside other artifacts, and deleting the tables of configurations the build no longer names is not this function's job.
#[allow(clippy::too_many_arguments)]
pub fn run_configs_tables(
    index: &SpecIndex,
    configs: &[Configuration<'_>],
    modes: EnumerationModes,
    outdir: &Path,
    inputs: &str,
    workers: usize,
    report: Report,
    seeding: Seeding,
) -> Result<Vec<TableAnswer>, String> {
    std::fs::create_dir_all(outdir).map_err(|error| format!("{}: {error}", outdir.display()))?;
    let world = modes.world_token();
    let edited = Exclusion::of(index, seeding.edited.iter().copied())
        .with_classes(seeding.moved_classes.iter().copied());
    let default_seat = seeding
        .config_seed
        .then(|| configs.iter().position(|config| config.features.is_empty()))
        .flatten()
        .filter(|_| configs.len() > 1);
    let Some(default_seat) = default_seat else {
        return claim_all(configs, workers, |config| {
            let previous = load_seed(index, &seeding, config.token, &world, |key| {
                !edited.names(key)
            })?;
            let seed = Seed {
                bases: base_over(previous.as_ref(), edited.clone())
                    .into_iter()
                    .collect(),
                keep_memo: false,
            };
            let carried = base_over(previous.as_ref(), edited.clone())
                .into_iter()
                .collect();
            let file = memo_file(&seeding, outdir, config.token, &world, carried);
            run_config_tables(index, config, modes, outdir, inputs, report, seed, file)
                .map_err(|complaint| format!("{}: {complaint}", config.token))
        });
    };
    let default = &configs[default_seat];
    let previous_default = load_seed(index, &seeding, default.token, &world, |key| {
        !edited.names(key)
    })
    .map_err(|complaint| format!("{}: {complaint}", default.token))?;
    let pending = enumerate_config_tables(
        index,
        default,
        modes,
        report,
        Seed {
            bases: base_over(previous_default.as_ref(), edited.clone())
                .into_iter()
                .collect(),
            keep_memo: true,
        },
        memo_file(
            &seeding,
            outdir,
            default.token,
            &world,
            base_over(previous_default.as_ref(), edited.clone())
                .into_iter()
                .collect(),
        ),
    )
    .map_err(|complaint| format!("{}: {complaint}", default.token))?;
    let memo: Arc<MemoSnapshot> = Arc::clone(
        pending
            .memo
            .as_ref()
            .expect("a kept memo comes back from a trace-memo enumeration"),
    );
    let rest = delta_worklist(index, configs, default_seat);
    let finish_default = || {
        finish_config_tables(index, default, outdir, inputs, report, pending)
            .map_err(|complaint| format!("{}: {complaint}", default.token))
    };
    let (default_answer, mut answered) =
        claim_all_leading(&rest, workers, finish_default, |work: &DeltaWork<'_>| {
            let config = work.config;
            let unlocking = &work.unlocking;
            let behind_unlocking = Exclusion::of(index, unlocking.iter().copied());
            let previous_own = load_seed(index, &seeding, config.token, &world, |key| {
                behind_unlocking.names(key) && !edited.names(key)
            })?;
            let mut bases = vec![MemoBase {
                memo: Arc::clone(&memo),
                excluded: behind_unlocking,
            }];
            bases.extend(base_over(
                previous_default.as_ref(),
                Exclusion::of(index, unlocking.iter().chain(edited.runes()).copied())
                    .with_classes(edited.classes().iter().copied()),
            ));
            bases.extend(base_over(previous_own.as_ref(), edited.clone()));
            let carried = base_over(previous_own.as_ref(), edited.clone())
                .into_iter()
                .collect();
            let file = memo_file(&seeding, outdir, config.token, &world, carried);
            let seed = Seed {
                bases,
                keep_memo: false,
            };
            run_config_tables(index, config, modes, outdir, inputs, report, seed, file)
                .map_err(|complaint| format!("{}: {complaint}", config.token))
        })?;
    answered.push((default_seat, default_answer));
    Ok(seat_answers(answered, configs.len()))
}

/// Builds one configuration's tables on the current thread: [`enumerate_config_tables`] then [`finish_config_tables`]. The fixpoint reads what `seed` allows and writes `file`, when given, at its release point. The fold over the product builds the rule certificates with the enumeration's own [`WindowOptions`], so the formation guard is swept once. It writes the three artifact files and returns the digest. When asked, the timing lines are `enumerate[<config>]`, `memo[<config>]`, and `fold[<config>]`, after the census's `[c]` lines. The seeded fan-out calls the two halves separately so that `default`'s second half can run alongside the wave.
#[allow(clippy::too_many_arguments)]
pub fn run_config_tables(
    index: &SpecIndex,
    config: &Configuration<'_>,
    modes: EnumerationModes,
    outdir: &Path,
    inputs: &str,
    report: Report,
    seed: Seed,
    file: Option<MemoFile>,
) -> Result<TableAnswer, String> {
    let pending = enumerate_config_tables(index, config, modes, report, seed, file)?;
    finish_config_tables(index, config, outdir, inputs, report, pending)
}

/// One configuration between the two halves of its table build: enumerated, its memo file written, and holding what the fold and the file writes need.
///
/// It cannot cross threads, because the product's label pool holds `Rc<str>` ([`crate::stream`]) and the window options hold `Rc<FollowerMap>` ([`crate::options`]). That is why the second half runs on the thread that enumerated, and why a caller clones the memo out first: behind its [`Arc`], the memo is the only part of an enumeration that other configurations read. Only a configuration whose seed set `keep_memo` has a memo here. A delta's memo was written to its file and dropped at the fixpoint's release point.
struct EnumeratedTables<'i> {
    product: FixpointProduct,
    options: WindowOptions<'i>,
    memo: Option<Arc<MemoSnapshot>>,
    timed: Vec<String>,
}

/// [`run_config_tables`]'s first half: the fixpoint over what the seed allows, writing the memo file at the release point when one is named, and keeping the finished memo behind an [`Arc`] when the seed asks. The timing lines are `enumerate[<config>]`, which excludes the memo write, and `memo[<config>]`, which is the write, after the census's `[c]` lines.
fn enumerate_config_tables<'i>(
    index: &'i SpecIndex,
    config: &Configuration<'_>,
    modes: EnumerationModes,
    report: Report,
    seed: Seed,
    file: Option<MemoFile>,
) -> Result<EnumeratedTables<'i>, String> {
    let token = config.token;
    let mut timed: Vec<String> = Vec::new();
    let mut census: Vec<String> = Vec::new();
    let started = Instant::now();
    let fixpoint::TablesEnumeration {
        product,
        options,
        memo,
        memo_write,
    } = fixpoint::enumerate_for_tables(
        index,
        &config.features,
        modes,
        report.census.then_some(&mut census),
        seed,
        file,
    )?;
    timed.append(&mut census);
    if report.timings {
        timed.push(timing_line(
            &format!("enumerate[{token}]"),
            started
                .elapsed()
                .saturating_sub(memo_write.unwrap_or_default()),
        ));
        if let Some(wrote) = memo_write {
            timed.push(timing_line(&format!("memo[{token}]"), wrote));
        }
    }
    Ok(EnumeratedTables {
        product,
        options,
        memo: memo.map(Arc::new),
        timed,
    })
}

/// [`run_config_tables`]'s second half, on the thread that enumerated. It drops the memo first (a caller that needs it has already cloned it out of [`EnumeratedTables`]), then folds the product, writes the three artifact files, and computes the digest. A timed run adds `fold.prefixes[<config>]` and `fold.partition[<config>]` at millisecond precision, then `fold[<config>]`. These follow the first half's lines, so a configuration's lines read enumerate, memo, fold, whichever thread ran each half.
fn finish_config_tables(
    index: &SpecIndex,
    config: &Configuration<'_>,
    outdir: &Path,
    inputs: &str,
    report: Report,
    pending: EnumeratedTables<'_>,
) -> Result<TableAnswer, String> {
    let token = config.token;
    let EnumeratedTables {
        product,
        mut options,
        memo,
        mut timed,
    } = pending;
    drop(memo);
    let started = Instant::now();
    let folded = if report.timings {
        fold::fold_with_profile(index, product, &mut options, |phase, elapsed| {
            timed.push(fold_timing_line(&format!("fold.{phase}[{token}]"), elapsed));
        })?
    } else {
        fold::fold_with(index, product, &mut options)?
    };
    let settlement = outdir.join(format!("settlement-{token}.tsv"));
    write_text(&settlement, &artifacts::settlement_tsv(&folded.decision))?;
    let treaties = outdir.join(format!("treaties-{token}.tsv"));
    write_text(&treaties, &artifacts::treaty_tsv(&folded.treaty))?;
    let windows = outdir.join(format!("windows-{token}.tsv"));
    artifacts::write_windows(index, &folded.decision, inputs, &windows)
        .map_err(|error| format!("{}: {error}", windows.display()))?;
    let digest = artifacts::table_digest(index, &folded.decision, &folded.treaty);
    if report.timings {
        timed.push(timing_line(&format!("fold[{token}]"), started.elapsed()));
    }
    Ok(TableAnswer { digest, timed })
}

/// One configuration's string replay result: the walk's counts, and its census and timing lines when requested.
pub struct ReplayAnswer {
    pub report: replay::Report,
    pub timed: Vec<String>,
}

/// Replays every configuration's persisted rules over `universe`, at most `workers` at a time. Each configuration reads `<outdir>/settlement-<config>.tsv` back, walks the universe's texts, and checks the rules' first-match result against the engine's own settlement, window by window. The engine gets the enumeration's two world flags but not `deep_classes`, since a replay settles single windows, which have no grain. With a `memo_dir`, each passing walk writes its window memo there as `replay-windows-<config>.bin` ([`replay::Replay::write_window_memo`]). A walk that fails writes nothing. A walk that released its memo under the universe's ceiling fails instead of writing, and the `replay-strings` CLI rejects `--memo-dir` with `--memo-windows`, so no command line can request that.
#[allow(clippy::too_many_arguments)]
pub fn run_configs_replay(
    index: &SpecIndex,
    configs: &[Configuration<'_>],
    modes: EnumerationModes,
    outdir: &Path,
    universe: replay::Universe<'_>,
    workers: usize,
    report: Report,
    memo_dir: Option<&Path>,
) -> Result<Vec<ReplayAnswer>, String> {
    claim_all(configs, workers, |config| {
        run_config_replay(index, config, modes, outdir, universe, report, memo_dir)
            .map_err(|complaint| format!("{}: {complaint}", config.token))
    })
}

/// Where one configuration's window memo is written under `memo_dir`. `kernel_exec.replay_memo_dump` builds the same path.
pub fn replay_memo_path(memo_dir: &Path, token: &str) -> PathBuf {
    memo_dir.join(format!("replay-windows-{token}.bin"))
}

/// Replays one configuration: reads its rules back and walks the universe. When asked, it returns the census's `[c]` lines and a `replay[<config>]` timing line. With a `memo_dir`, it then writes the window memo, timed as `replay_memo[<config>]`. The walk's clock stops before the census is taken, so the end-of-walk resident-size sample is not counted in the walk's time; the samples a censused walk takes at each release under the universe's ceiling are.
pub fn run_config_replay(
    index: &SpecIndex,
    config: &Configuration<'_>,
    modes: EnumerationModes,
    outdir: &Path,
    universe: replay::Universe<'_>,
    report: Report,
    memo_dir: Option<&Path>,
) -> Result<ReplayAnswer, String> {
    let token = config.token;
    let started = Instant::now();
    let settlement = outdir.join(format!("settlement-{token}.tsv"));
    let text = std::fs::read_to_string(&settlement)
        .map_err(|error| format!("{}: {error}", settlement.display()))?;
    let rules = artifacts::read_settlement_tsv(&text)
        .map_err(|complaint| format!("{}: {complaint}", settlement.display()))?;
    let engine_modes = EngineModes {
        simulated_prospect: modes.simulated_prospect,
        vote_slots: modes.vote_slots,
        ..EngineModes::default()
    };
    let mut walk = replay::Replay::new(index, config.features.clone(), engine_modes, &rules);
    if report.census {
        walk.with_census(token);
    }
    let walked = walk.walk_universe(universe)?;
    let elapsed = started.elapsed();
    let mut timed = walk.take_census();
    if report.timings {
        timed.push(timing_line(&format!("replay[{token}]"), elapsed));
    }
    if let Some(memo_dir) = memo_dir {
        let started = Instant::now();
        walk.write_window_memo(&replay_memo_path(memo_dir, token), token, universe.horizon)?;
        if report.timings {
            timed.push(timing_line(
                &format!("replay_memo[{token}]"),
                started.elapsed(),
            ));
        }
    }
    Ok(ReplayAnswer {
        report: walked,
        timed,
    })
}

fn write_text(path: &Path, text: &str) -> Result<(), String> {
    std::fs::write(path, text).map_err(|error| format!("{}: {error}", path.display()))
}

/// Runs one configuration into its own file under `outdir`. Every error names the configuration, since a caller running several has no other way to tell which one failed, and a filesystem error also names the file.
fn into_file(
    index: &SpecIndex,
    config: &Configuration<'_>,
    modes: EnumerationModes,
    outdir: &Path,
    report: Report,
) -> Result<Vec<String>, String> {
    let path = transitions_path(outdir, config.token);
    let mut file = std::fs::File::create(&path)
        .map_err(|error| format!("{}: {}: {error}", config.token, path.display()))?;
    run_config(index, config, modes, &mut file, report).map_err(|failure| match failure {
        Failure::Refused(complaint) => format!("{}: {complaint}", config.token),
        Failure::Sink(error) => format!("{}: {}: {error}", config.token, path.display()),
    })
}

/// Removes every `transitions-*.ndjson` file under `outdir` that this run does not name, before any configuration writes.
///
/// It removes only regular files that match the pattern [`transitions_path`] produces, and leaves everything else in the directory alone.
fn sweep_unnamed_streams(outdir: &Path, configs: &[Configuration<'_>]) -> Result<(), String> {
    let named: BTreeSet<&str> = configs.iter().map(|config| config.token).collect();
    let listing =
        std::fs::read_dir(outdir).map_err(|error| format!("{}: {error}", outdir.display()))?;
    for entry in listing {
        let entry = entry.map_err(|error| format!("{}: {error}", outdir.display()))?;
        let name = entry.file_name();
        let Some(token) = name
            .to_str()
            .and_then(|name| name.strip_prefix(STREAM_PREFIX))
            .and_then(|name| name.strip_suffix(STREAM_SUFFIX))
        else {
            continue;
        };
        if named.contains(token) || !entry.file_type().is_ok_and(|kind| kind.is_file()) {
            continue;
        }
        let path = entry.path();
        std::fs::remove_file(&path).map_err(|error| format!("{}: {error}", path.display()))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;

    /// The shipping world (the [`EnumerationModes`] default), in which the deep slots enumerate at class grain. The tests below check byte-identity across schedules in this world.
    const SHIPPING: EnumerationModes = EnumerationModes {
        simulated_prospect: true,
        vote_slots: true,
        deep_classes: true,
    };

    /// Two configurations the fixture tells apart: `ss03` unlocks a `qsMay` entry, and `default` unlocks nothing.
    const TOKENS: [&str; 2] = ["default", "ss03"];

    /// A set whose claim order differs from its listed order: `default`; `ss09`, a delta that unlocks nothing; and `ss03`, the delta that unlocks `qsMay`, which the wave claims first although it is listed last. `ss09` has no features because the fixture declares no second feature. It still runs as a delta, because only the first no-feature configuration seeds.
    const PERMUTING: [&str; 3] = ["default", "ss09", "ss03"];

    /// A scratch directory for one test under `target/test-scratch`, which is gitignored. It is cleared first so a stale file cannot pass for one the run should have written, and it is not created, because creating it is part of what the run does.
    fn scratch(name: &str) -> PathBuf {
        let directory = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target/test-scratch")
            .join(name);
        let _ = std::fs::remove_dir_all(&directory);
        directory
    }

    fn configurations(index: &SpecIndex) -> Vec<Configuration<'static>> {
        configurations_of(index, &TOKENS)
    }

    fn permuting(index: &SpecIndex) -> Vec<Configuration<'static>> {
        configurations_of(index, &PERMUTING)
    }

    /// The tokens as configurations: `default` and `ss09` with no features, anything else with the feature its token names.
    fn configurations_of(
        index: &SpecIndex,
        tokens: &[&'static str],
    ) -> Vec<Configuration<'static>> {
        tokens
            .iter()
            .map(|token| Configuration {
                token,
                features: if *token == "default" || *token == "ss09" {
                    Vec::new()
                } else {
                    vec![fixtures::sym(index, token)]
                },
            })
            .collect()
    }

    /// The bytes `enumerate` writes for one configuration: [`run_config`] into a buffer instead of stdout.
    fn enumerated(index: &SpecIndex, config: &Configuration<'_>) -> String {
        let mut sink: Vec<u8> = Vec::new();
        run_config(index, config, SHIPPING, &mut sink, Report::timed(false))
            .expect("the fixture's fixpoint closes and serializes");
        String::from_utf8(sink).expect("a transitions stream is text")
    }

    /// Puts a directory at a configuration's stream path, so a worker fails during the run instead of before it.
    fn block(outdir: &Path, token: &str) {
        std::fs::create_dir_all(transitions_path(outdir, token))
            .expect("a directory can occupy a stream's path");
    }

    /// The fan-out, at one thread and at more threads than configurations, writes the same bytes as enumerating each configuration alone.
    ///
    /// This is the thread-count invariant in the module doc at fixture scale: a concurrent run's files match a serial run's byte for byte, because the configurations share only a read-only spec.
    #[test]
    fn a_fan_out_writes_the_bytes_one_enumeration_at_a_time_writes() {
        let index = fixtures::mini();
        let configs = configurations(&index);
        let expected: Vec<String> = configs
            .iter()
            .map(|config| enumerated(&index, config))
            .collect();
        let root = scratch("fan-out");
        for workers in [1, 8] {
            let outdir = root.join(format!("at-{workers}")).join("streams");
            let timed = run_configs(
                &index,
                &configs,
                SHIPPING,
                &outdir,
                workers,
                Report::timed(false),
            )
            .expect("every configuration answers");
            assert_eq!(timed.len(), configs.len());
            for (config, expected) in configs.iter().zip(&expected) {
                let written = std::fs::read_to_string(transitions_path(&outdir, config.token))
                    .expect("every named configuration left a file behind");
                assert_eq!(
                    &written, expected,
                    "{} at {workers} threads is not the bytes one enumeration writes",
                    config.token
                );
            }
        }
        std::fs::remove_dir_all(&root).expect("the scratch directory is removable");
    }

    /// Timing lines come back in the caller's configuration order at any thread count and name both phases of every configuration.
    #[test]
    fn the_timing_lines_come_back_in_the_order_the_configurations_were_named() {
        let index = fixtures::mini();
        let configs = configurations(&index);
        let root = scratch("fan-out-timings");
        for workers in [1, 8] {
            let outdir = root.join(format!("at-{workers}")).join("streams");
            let timed = run_configs(
                &index,
                &configs,
                SHIPPING,
                &outdir,
                workers,
                Report::timed(true),
            )
            .expect("every configuration answers");
            let labels: Vec<String> = timed
                .iter()
                .flatten()
                .map(|line| {
                    line.split(' ')
                        .nth(1)
                        .expect("a timing line names its phase")
                        .to_owned()
                })
                .collect();
            assert_eq!(
                labels,
                [
                    "enumerate[default]",
                    "emit[default]",
                    "enumerate[ss03]",
                    "emit[ss03]"
                ]
            );
        }
        std::fs::remove_dir_all(&root).expect("the scratch directory is removable");
    }

    /// A run without `--timings` records no timing lines, so `kernel_exec._forward_stderr` can treat any stderr on a clean exit as a failure.
    #[test]
    fn a_run_that_was_not_asked_to_time_itself_records_nothing() {
        let index = fixtures::mini();
        let configs = configurations(&index);
        let outdir = scratch("fan-out-untimed");
        let timed = run_configs(&index, &configs, SHIPPING, &outdir, 2, Report::timed(false))
            .expect("every configuration answers");
        assert!(timed.iter().all(Vec::is_empty));
        std::fs::remove_dir_all(&outdir).expect("the scratch directory is removable");
    }

    /// The `[t]` line formats that `console.INNER_LINE` parses: ordinary phases at one decimal and fold subphases at millisecond precision.
    #[test]
    fn timing_lines_use_the_shapes_the_cycle_parses() {
        assert_eq!(
            timing_line("spec_parse", Duration::from_millis(1234)),
            "[t] spec_parse 1.2s"
        );
        assert_eq!(
            timing_line("enumerate[ss03+ss05]", Duration::from_millis(90_100)),
            "[t] enumerate[ss03+ss05] 90.1s"
        );
        assert_eq!(
            timing_line("emit[default]", Duration::from_millis(4)),
            "[t] emit[default] 0.0s"
        );
        assert_eq!(
            timing_line("enumerate_total", Duration::from_secs(75)),
            "[t] enumerate_total 75.0s"
        );
        assert_eq!(
            fold_timing_line("fold.partition[default]", Duration::from_micros(1234)),
            "[t] fold.partition[default] 0.001s"
        );
    }

    /// A configuration's stream is named `transitions-<token>.ndjson` with the caller's token unchanged, so a caller knows every file name before the run starts.
    #[test]
    fn a_configuration_files_its_stream_under_its_own_token() {
        assert_eq!(
            transitions_path(Path::new("out"), "ss03+ss05"),
            Path::new("out/transitions-ss03+ss05.ndjson")
        );
    }

    /// A stream that cannot be created stops the run, and the error names both the configuration and the path.
    #[test]
    fn a_stream_that_cannot_be_created_stops_the_run() {
        let index = fixtures::mini();
        let configs = configurations(&index);
        let outdir = scratch("fan-out-blocked");
        std::fs::create_dir_all(&outdir).expect("the scratch directory is makeable");
        block(&outdir, TOKENS[0]);
        let complaint = run_configs(&index, &configs, SHIPPING, &outdir, 1, Report::timed(false))
            .expect_err("a directory in a stream's place is not writable");
        assert!(
            complaint.starts_with(&format!("{}: ", TOKENS[0])),
            "the complaint names the configuration that failed: {complaint}"
        );
        assert!(
            complaint.contains(&format!("transitions-{}.ndjson", TOKENS[0])),
            "and the file it failed on: {complaint}"
        );
        std::fs::remove_dir_all(&outdir).expect("the scratch directory is removable");
    }

    /// With every stream path blocked and one worker per configuration, the run reports the error at the earliest list position, whichever worker reached it and however many others also failed. Position 0 is always claimed: the first claim takes it, and a worker stops claiming only after some worker has failed.
    #[test]
    fn the_complaint_a_run_reports_is_the_earliest_seated_one() {
        let index = fixtures::mini();
        let configs = configurations(&index);
        let outdir = scratch("fan-out-all-blocked");
        std::fs::create_dir_all(&outdir).expect("the scratch directory is makeable");
        for token in TOKENS {
            block(&outdir, token);
        }
        let complaint = run_configs(
            &index,
            &configs,
            SHIPPING,
            &outdir,
            configs.len(),
            Report::timed(false),
        )
        .expect_err("no seat can write its stream");
        assert!(
            complaint.starts_with(&format!("{}: ", TOKENS[0])),
            "the earliest seat is the one reported: {complaint}"
        );
        std::fs::remove_dir_all(&outdir).expect("the scratch directory is removable");
    }

    /// A run removes the streams of configurations it was not asked for, so a directory globbed after a clean exit holds only this run's output, and it leaves files that are not streams alone.
    #[test]
    fn a_run_sweeps_the_streams_it_did_not_name() {
        let index = fixtures::mini();
        let configs = configurations(&index);
        let outdir = scratch("fan-out-sweep");
        std::fs::create_dir_all(&outdir).expect("the scratch directory is makeable");
        let stale = transitions_path(&outdir, "ss09");
        std::fs::write(&stale, "a configuration this run was not asked about\n")
            .expect("the scratch directory takes a file");
        let bystander = outdir.join("manifest.json");
        std::fs::write(&bystander, "{}\n").expect("and another that is not a stream");
        run_configs(&index, &configs, SHIPPING, &outdir, 2, Report::timed(false))
            .expect("every configuration answers");
        assert!(
            !stale.exists(),
            "the unnamed configuration's stream is gone"
        );
        assert!(bystander.exists(), "and nothing else was touched");
        for config in &configs {
            assert!(transitions_path(&outdir, config.token).exists());
        }
        std::fs::remove_dir_all(&outdir).expect("the scratch directory is removable");
    }

    /// A thread count of 0 runs one worker: the count caps concurrency, and a cap of 0 must not mean a run that does nothing.
    #[test]
    fn a_run_with_no_workers_named_still_answers_every_configuration() {
        let index = fixtures::mini();
        let configs = configurations(&index);
        let outdir = scratch("fan-out-no-workers");
        let timed = run_configs(&index, &configs, SHIPPING, &outdir, 0, Report::timed(false))
            .expect("the run happens");
        assert_eq!(timed.len(), configs.len());
        for config in &configs {
            assert!(transitions_path(&outdir, config.token).exists());
        }
        std::fs::remove_dir_all(&outdir).expect("the scratch directory is removable");
    }

    /// The inputs stamp every table build below writes its window files under.
    const INPUTS: &str = "fixture-stamp";

    /// A seeding that writes memo files, so every configuration writes one at its release point and `default` writes its file before the wave reads its snapshot.
    fn stamped() -> Seeding {
        Seeding {
            memo_stamp: Some("identity".to_owned()),
            ..Seeding::default()
        }
    }

    /// What one width of the table fan-out wrote: every configuration's digest, and every configuration's files as [`table_files`] reads them.
    type Filed = (Vec<String>, Vec<Vec<(String, Vec<u8>)>>);

    /// The files one configuration's table build writes, read back as bytes: the three tables, plus the memo file when the build had a stamp.
    fn table_files(outdir: &Path, token: &str, memo: bool) -> Vec<(String, Vec<u8>)> {
        let mut names: Vec<String> = ["settlement", "treaties", "windows"]
            .iter()
            .map(|family| format!("{family}-{token}.tsv"))
            .collect();
        if memo {
            names.push(format!("memo-{token}.tsv"));
        }
        names
            .into_iter()
            .map(|name| {
                let bytes = std::fs::read(outdir.join(&name))
                    .unwrap_or_else(|error| panic!("{name} was filed: {error}"));
                (name, bytes)
            })
            .collect()
    }

    /// Asserts that `written` and `expected` hold the same files position by position, with the same names and the same bytes. Checking the name means a file only one side wrote fails on its name instead of being compared with a different file.
    fn same_files(
        written: &[Vec<(String, Vec<u8>)>],
        expected: &[Vec<(String, Vec<u8>)>],
        run: &str,
        against: &str,
    ) {
        assert_eq!(
            written.len(),
            expected.len(),
            "{run}: one entry per configuration"
        );
        for (written, expected) in written.iter().zip(expected) {
            assert_eq!(
                written.len(),
                expected.len(),
                "{run}: the files filed per configuration"
            );
            for ((name, written), (expected_name, expected)) in written.iter().zip(expected) {
                assert_eq!(name, expected_name, "{run}: the files pair by name");
                assert!(
                    written == expected,
                    "{run}: {name} is not the bytes {against} filed"
                );
            }
        }
    }

    /// Puts a directory at a configuration's settlement table path, which makes its build fail in the second half: after its enumeration and, for `default`, after the wave has started.
    fn block_settlement(outdir: &Path, token: &str) {
        std::fs::create_dir_all(outdir.join(format!("settlement-{token}.tsv")))
            .expect("a directory can occupy a settlement table's path");
    }

    /// The table fan-out writes the same bytes at every width, over the set whose claim order differs from its listed order. Tables, memo files, and digests match per configuration at 0, 1, 2, and 8 workers. At 2, one worker takes the heavy delta and the lead claims the cheap one after `default`'s fold, which is the case the claim order is for. At 8, the deltas enumerate while `default`'s fold runs on the calling thread. In the stamped case, every configuration writes its memo file at its release point, and those files are compared too. The stamped case's tables and digests must also match the unstamped case's at the same width, which shows that writing the memo file changes no table.
    #[test]
    fn a_table_fan_out_files_the_same_bytes_at_every_width() {
        let index = fixtures::mini();
        let configs = permuting(&index);
        let root = scratch("fan-out-tables");
        let mut unstamped: Vec<Filed> = Vec::new();
        for (arm, seeding) in [("unstamped", Seeding::default()), ("stamped", stamped())] {
            let with_memo = seeding.memo_stamp.is_some();
            let mut first: Option<Filed> = None;
            for (width, workers) in [0, 1, 2, 8].into_iter().enumerate() {
                let outdir = root.join(arm).join(format!("at-{workers}"));
                let answers = run_configs_tables(
                    &index,
                    &configs,
                    SHIPPING,
                    &outdir,
                    INPUTS,
                    workers,
                    Report::timed(false),
                    seeding.clone(),
                )
                .expect("every configuration answers");
                assert_eq!(answers.len(), configs.len());
                let digests: Vec<String> =
                    answers.iter().map(|answer| answer.digest.clone()).collect();
                let files: Vec<_> = configs
                    .iter()
                    .map(|config| table_files(&outdir, config.token, with_memo))
                    .collect();
                if let Some((expected_digests, expected_files)) = &first {
                    assert_eq!(&digests, expected_digests, "{arm} at {workers} workers");
                    same_files(
                        &files,
                        expected_files,
                        &format!("{arm} at {workers} workers"),
                        "the first width",
                    );
                } else {
                    first = Some((digests.clone(), files.clone()));
                }
                if !with_memo {
                    unstamped.push((digests, files));
                    continue;
                }
                let (plain_digests, plain_files) = unstamped
                    .get(width)
                    .expect("the unstamped arm files every width before the stamped one runs");
                assert_eq!(
                    &digests, plain_digests,
                    "stamped at {workers} workers: the digests the unstamped arm filed"
                );
                let tables: Vec<Vec<(String, Vec<u8>)>> = files
                    .iter()
                    .map(|written| {
                        written
                            .iter()
                            .filter(|(name, _)| !name.starts_with("memo-"))
                            .cloned()
                            .collect()
                    })
                    .collect();
                same_files(
                    &tables,
                    plain_files,
                    &format!("stamped at {workers} workers"),
                    "the unstamped arm",
                );
            }
        }
        std::fs::remove_dir_all(&root).expect("the scratch directory is removable");
    }

    /// A table build's timing lines come back in the caller's configuration order, with each configuration's phases in the order they ran, at any width. `default`'s fold lines follow its enumerate and memo lines even when the fold ran alongside the deltas, and `ss03`'s lines come last although the wave claims it first.
    #[test]
    fn a_table_build_times_its_phases_in_configuration_order_at_any_width() {
        let index = fixtures::mini();
        let configs = permuting(&index);
        let root = scratch("fan-out-tables-timings");
        for workers in [1, 2, 8] {
            let outdir = root.join(format!("at-{workers}"));
            let answers = run_configs_tables(
                &index,
                &configs,
                SHIPPING,
                &outdir,
                INPUTS,
                workers,
                Report::timed(true),
                stamped(),
            )
            .expect("every configuration answers");
            let labels: Vec<String> = answers
                .iter()
                .flat_map(|answer| &answer.timed)
                .map(|line| {
                    line.split(' ')
                        .nth(1)
                        .expect("a timing line names its phase")
                        .to_owned()
                })
                .collect();
            assert_eq!(
                labels,
                [
                    "enumerate[default]",
                    "memo[default]",
                    "fold.prefixes[default]",
                    "fold.partition[default]",
                    "fold[default]",
                    "enumerate[ss09]",
                    "memo[ss09]",
                    "fold.prefixes[ss09]",
                    "fold.partition[ss09]",
                    "fold[ss09]",
                    "enumerate[ss03]",
                    "memo[ss03]",
                    "fold.prefixes[ss03]",
                    "fold.partition[ss03]",
                    "fold[ss03]"
                ],
                "at {workers} workers"
            );
        }
        std::fs::remove_dir_all(&root).expect("the scratch directory is removable");
    }

    /// If `default`'s second half fails while the wave runs, the run reports `default`'s error. When only `default` is blocked, the error names the file it failed on. When every configuration is blocked, the run reports `default`'s error instead of the delta's, whether or not the delta was claimed before the stop. `a_lead_runs_beside_the_seat_that_claims_while_it_does` checks that the lead overlaps the workers; this test checks only which error the run returns.
    #[test]
    fn a_default_that_fails_during_the_wave_is_what_the_run_reports() {
        let index = fixtures::mini();
        let configs = configurations(&index);
        let root = scratch("fan-out-tables-blocked");
        let alone = root.join("default-alone");
        block_settlement(&alone, TOKENS[0]);
        let complaint = run_configs_tables(
            &index,
            &configs,
            SHIPPING,
            &alone,
            INPUTS,
            2,
            Report::timed(false),
            Seeding::default(),
        )
        .expect_err("a directory in the settlement table's place is not writable");
        assert!(
            complaint.starts_with(&format!("{}: ", TOKENS[0])),
            "the complaint names the configuration that failed: {complaint}"
        );
        assert!(
            complaint.contains(&format!("settlement-{}.tsv", TOKENS[0])),
            "and the file it failed on: {complaint}"
        );
        let every = root.join("every-seat");
        for token in TOKENS {
            block_settlement(&every, token);
        }
        let complaint = run_configs_tables(
            &index,
            &configs,
            SHIPPING,
            &every,
            INPUTS,
            2,
            Report::timed(false),
            Seeding::default(),
        )
        .expect_err("no seat can write its settlement table");
        assert!(
            complaint.starts_with(&format!("{}: ", TOKENS[0])),
            "default's word wins over a delta that failed beside it: {complaint}"
        );
        std::fs::remove_dir_all(&root).expect("the scratch directory is removable");
    }

    /// The wave's worklist over the permuting set: `ss03`, the delta that unlocks `qsMay`, comes first with position 2, and the delta that unlocks nothing follows with position 1. `default` is not in it, and each entry holds its unlocking runes.
    #[test]
    fn the_delta_worklist_is_claimed_heaviest_first() {
        let index = fixtures::mini();
        let configs = permuting(&index);
        let work = delta_worklist(&index, &configs, 0);
        let seated: Vec<(usize, &str)> = work
            .iter()
            .map(|(seat, work)| (*seat, work.config.token))
            .collect();
        assert_eq!(seated, [(2, "ss03"), (1, "ss09")]);
        let may = fixtures::sym(&index, "qsMay");
        assert_eq!(work[0].1.unlocking.len(), 1);
        assert!(work[0].1.unlocking.contains(&may));
        assert!(work[1].1.unlocking.is_empty());
    }

    /// A failing item's error is reported at the position the item carries, not the order it was claimed in. The test uses a barrier instead of relying on timing: over a worklist listed out of order, each item fails only after both items reach the barrier, so both are claimed before either failure stops claiming, whichever of the three workers claims them. The run reports the position-1 item's error although the position-3 item was claimed first.
    #[test]
    fn a_failing_item_is_reported_at_the_seat_it_carries() {
        let work = [(3, "ss03"), (1, "ss09")];
        let met = std::sync::Barrier::new(2);
        let complaint = claim_all_leading(
            &work,
            3,
            || Ok(()),
            |token: &&str| {
                met.wait();
                Err::<(), _>(format!("{token}: blocked"))
            },
        )
        .expect_err("both items fail");
        assert_eq!(complaint, "ss09: blocked");
    }

    /// The lead runs while another worker claims, which the test enforces with a barrier instead of relying on timing: with two workers and a worklist listed out of order, the lead waits at a barrier that the first item's answer also reaches, so the worker has claimed and started an item while the lead is still running. `default`'s fold relies on this overlap. The run returns both items' results with the positions they carry, and the lead's result separately. When both fail, the run returns the lead's error.
    #[test]
    fn a_lead_runs_beside_the_seat_that_claims_while_it_does() {
        let work = [(3, "ss03"), (1, "ss09")];
        let met = std::sync::Barrier::new(2);
        let (led, mut seated) = claim_all_leading(
            &work,
            2,
            || {
                met.wait();
                Ok("lead")
            },
            |token: &&str| {
                if *token == "ss03" {
                    met.wait();
                }
                Ok((*token).to_owned())
            },
        )
        .expect("both seats answer");
        seated.sort_by_key(|(seat, _)| *seat);
        assert_eq!(led, "lead");
        assert_eq!(seated, [(1, "ss09".to_owned()), (3, "ss03".to_owned())]);
        let met = std::sync::Barrier::new(2);
        let complaint = claim_all_leading(
            &work,
            2,
            || {
                met.wait();
                Err::<(), _>("lead: blocked".to_owned())
            },
            |token: &&str| {
                if *token == "ss03" {
                    met.wait();
                }
                Err::<(), _>(format!("{token}: blocked"))
            },
        )
        .expect_err("both seats fail");
        assert_eq!(complaint, "lead: blocked");
    }
}
