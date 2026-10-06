//! Runs settlement configurations: one into a caller's sink, or a named set concurrently into a directory of files. The `enumerate` and `enumerate-configs` subcommands both serialize a configuration through [`run_config`], so a file the fan-out writes cannot differ from what `enumerate` writes to stdout for the same configuration.
//!
//! The output does not depend on the thread count. All configurations share one [`SpecIndex`], which has no mutable state. The engine, the window options, the two slot filters, the liveness probe, and the fiber deriver are built inside the fixpoint on each call, so each configuration has its own. Enumerations share only read-only memo snapshots behind an [`Arc`], each read through an exclusion that says which of its windows a reader may use ([`crate::memo`]): `default`'s finished memo, and, when the previous memo directory has one, the previous build's `default` memo. `default`'s enumeration finishes before any delta starts. Its memo file write finishes inside it, unless the build writes its memo files beside the rest of the build ([`MemoSharing`]); a write reads only the finished snapshot and what it carries, and the deltas read `default`'s snapshot, never its file, so the file's bytes do not depend on when it is written. Its fold preparation then runs in one of the wave's worker slots and does not use the memo. A table build's folds also share what [`crate::crossconfig`] exchanges between them, each configuration's published rules and the rows it sends the others, read only at the rendezvous [`run_configs_tables`] describes, so no fold depends on when another finished.
//!
//! Parallelism stops at the configuration. A configuration's product depends only on its row set, not on the order the worklist visits windows, because a class-grain row is traced at its fiber's canonical representative. The fixpoint keeps its LIFO worklist order fixed anyway. A configuration's `cited_provenance` is what its one engine fired while tracing its windows. Splitting one configuration's worklist across threads would require the threads to share one engine's memo and fired set to reproduce the sequential result, which would cost what the split was meant to save.

use std::cmp::Reverse;
use std::collections::BTreeSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::thread::{Scope, ScopedJoinHandle};
use std::time::{Duration, Instant};

use crate::artifacts;
use crate::crossconfig::{self, ForeignRow, Imports, SharedRules, Source, SourceRows, Spellings};
use crate::engine::EngineModes;
use crate::fixpoint::{self, EnumerationModes, MemoAccess, MemoWrite};
use crate::fold;
use crate::hash::HashSet;
use crate::index::SpecIndex;
use crate::memo::{
    Exclusion, MemoFile, MemoHead, MemoSnapshot, SharedMemo, memo_path, read_memo, unlocking_runes,
    write_memo,
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
/// - `default_memo_sharing`: `default` enumerates first, alone, and every other configuration then reads `default`'s finished memo for the windows that name none of its own unlocking runes. When it is off, no configuration reads `default`'s in-process memo; `tests/cli.rs` checks that both settings write the same tables.
/// - `previous_memos`: a previous build's memo files, each read as a shared memo for its own configuration behind an exclusion of `edited` (the runes whose content changed since that build) and `moved_classes` (the predicate classes whose membership changed). A configuration with no file there reads nothing.
/// - `memo_stamp`: the stamp this build writes its own memo files under, beside its tables. With no stamp, no memo files are written.
/// - `overlap_memo_writes`: in a build that shares `default`'s memo, each configuration's memo file is written on a thread of its own from the configuration's release point, beside its drain, sort and fold and the rest of the wave, instead of on the configuration's own thread ahead of them ([`run_configs_tables`]). The files are the same bytes either way. A build in which every configuration enumerates from scratch writes each file ahead of its drain whatever this says.
#[derive(Clone, Debug)]
pub struct MemoSharing {
    pub default_memo_sharing: bool,
    pub previous_memos: Option<PathBuf>,
    pub edited: Vec<Sym>,
    pub moved_classes: Vec<Sym>,
    pub memo_stamp: Option<String>,
    pub overlap_memo_writes: bool,
}

impl Default for MemoSharing {
    fn default() -> Self {
        Self {
            default_memo_sharing: true,
            previous_memos: None,
            edited: Vec::new(),
            moved_classes: Vec::new(),
            memo_stamp: None,
            overlap_memo_writes: false,
        }
    }
}

/// A previous build's memo for one configuration, read from the previous memo directory and filtered through `keep`, or `None` when the directory has no file for it. A file that exists but belongs to another configuration or mode set is an error, because the caller named the previous memo directory so that it would be read.
fn load_previous_memo(
    index: &SpecIndex,
    sharing: &MemoSharing,
    token: &str,
    modes: &str,
    keep: impl Fn(&crate::engine::TraceKey) -> bool,
) -> Result<Option<Arc<MemoSnapshot>>, String> {
    let Some(dir) = &sharing.previous_memos else {
        return Ok(None);
    };
    let path = memo_path(dir, token);
    if !path.is_file() {
        return Ok(None);
    }
    let expected = MemoHead {
        config: token.to_owned(),
        modes: modes.to_owned(),
        stamp: String::new(),
    };
    read_memo(index, &path, &expected, keep).map(|memo| Some(Arc::new(memo)))
}

/// The memo file one configuration writes under this memo sharing, or `None` when the build has no stamp to write under.
fn memo_file(
    sharing: &MemoSharing,
    outdir: &Path,
    token: &str,
    modes: &str,
    carried: Vec<SharedMemo>,
) -> Option<MemoFile> {
    sharing.memo_stamp.as_ref().map(|stamp| MemoFile {
        path: memo_path(outdir, token),
        head: MemoHead {
            config: token.to_owned(),
            modes: modes.to_owned(),
            stamp: stamp.clone(),
        },
        carried,
    })
}

/// A shared memo over a previous memo behind an exclusion, or `None` when there is no previous memo.
fn shared_behind(memo: Option<&Arc<MemoSnapshot>>, excluded: Exclusion) -> Option<SharedMemo> {
    memo.map(|memo| SharedMemo {
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

/// The cap on a caller's `--threads`: the machine's available parallelism, or 1 when it reports none. It ignores QoS: under `taskpolicy -b`, which is how `make test-slowly` runs, it returns the full logical core count, not the count of cores that background priority confines the tree to (the Makefile comment on `test-slowly` says which cores those are).
pub fn available_threads() -> usize {
    std::thread::available_parallelism().map_or(1, std::num::NonZero::get)
}

/// One phase's wall-clock time as `[t] <label> <secs>s` at one decimal. `INNER_LINE` in `rebuild/tools/console.py` is the pattern `cycle_timings.py` parses these lines with.
pub fn timing_line(label: &str, elapsed: Duration) -> String {
    format!("[t] {label} {:.1}s", elapsed.as_secs_f64())
}

/// One fold subphase's wall-clock time at millisecond precision, so a short subphase such as the row-chain search does not round to zero.
fn fold_timing_line(label: &str, elapsed: Duration) -> String {
    format!("[t] {label} {:.3}s", elapsed.as_secs_f64())
}

/// What a run writes to stderr besides its output: phase timings and the `--cache-stats` lines. Both are off by default. A run with neither writes nothing to stderr on a clean exit. `_forward_stderr` in rebuild/pipeline/kernel_exec.py relies on this: when timings were not requested, it fails on any stderr after a clean exit.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub timings: bool,
    pub cache_stats: bool,
}

impl Report {
    /// A report with timings as given and no cache stats.
    pub fn timed(timings: bool) -> Self {
        Self {
            timings,
            cache_stats: false,
        }
    }

    /// Whether this run writes nothing to stderr.
    pub fn silent(self) -> bool {
        !self.timings && !self.cache_stats
    }
}

/// Runs one configuration into `sink`: its fixpoint, then its stream. When asked, it returns the cache stats' `[c]` lines followed by `enumerate[<config>]` and `emit[<config>]` timing lines. Errors carry no prefix; [`Failure`] says why.
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
    let mut stats: Vec<String> = Vec::new();
    let started = Instant::now();
    let product = if report.cache_stats {
        fixpoint::enumerate_with_cache_stats(index, &config.features, modes, &mut stats)
    } else {
        fixpoint::enumerate_transitions(index, &config.features, modes)
    }
    .map_err(Failure::Refused)?;
    timed.append(&mut stats);
    if timings {
        timed.push(timing_line(
            &format!("enumerate[{token}]"),
            started.elapsed(),
        ));
    }
    let started = Instant::now();
    stream::write_transitions(index, &product, sink).map_err(|failure| match failure {
        stream::WriteFailure::Refused(error) => Failure::Refused(error),
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
/// Each worker claims the next configuration from a shared counter, runs it, and claims again, so one worker walks the list in order and several share it without a plan ([`claim_positions`]). Each result carries its configuration's list position and is put back in list order by that position, so the caller's stdout and stderr do not depend on scheduling.
///
/// A `workers` of 0 runs one worker: the count caps concurrency, and walking the list takes at least one. The first failure stops further claims, since the output of a failed run is not usable anyway. The error returned is the one at the earliest list position among the failures that occurred.
fn claim_all<T: Send>(
    configs: &[Configuration<'_>],
    workers: usize,
    answer: impl Fn(&Configuration<'_>) -> Result<T, String> + Sync,
) -> Result<Vec<T>, String> {
    let work: Vec<(usize, &Configuration<'_>)> = configs.iter().enumerate().collect();
    claim_positions(&work, workers, |config| answer(config))
        .map(|placed| place_answers(placed, configs.len()))
}

/// [`claim_all`]'s scheduler over any worklist, `workers` wide. Each worklist item carries the position its result belongs at, and results come back as `(position, result)` pairs, so a caller can order its worklist by cost and still place every result where it was listed ([`place_answers`] turns the pairs into a list). The first failure stops further claims. The error returned is from the failed item with the earliest carried position, which may differ from the earliest claimed.
fn claim_positions<W: Sync, T: Send>(
    work: &[(usize, W)],
    workers: usize,
    answer: impl Fn(&W) -> Result<T, String> + Sync,
) -> Result<Vec<(usize, T)>, String> {
    let next = AtomicUsize::new(0);
    let stop = AtomicBool::new(false);
    let answer = &answer;
    let claimed: Vec<_> = std::thread::scope(|scope| {
        let handles: Vec<_> = (0..workers.max(1))
            .map(|_| scope.spawn(|| claim_items(work, &next, &stop, answer)))
            .collect();
        handles
            .into_iter()
            .map(|handle| {
                handle
                    .join()
                    .unwrap_or_else(|panic| std::panic::resume_unwind(panic))
            })
            .collect()
    });
    let mut placed: Vec<(usize, T)> = Vec::with_capacity(work.len());
    let mut failure: Option<(usize, String)> = None;
    for outcome in claimed {
        match outcome {
            Ok(answered) => placed.extend(answered),
            Err((listed, error)) => {
                if failure.as_ref().is_none_or(|(worst, _)| listed < *worst) {
                    failure = Some((listed, error));
                }
            }
        }
    }
    match failure {
        Some((_, error)) => Err(error),
        None => Ok(placed),
    }
}

/// Turns `(position, result)` pairs into one result per position, in position order. A run that reported no failure has a result for every position, since claiming stops only on a failure or at the end of the list.
fn place_answers<T>(answered: Vec<(usize, T)>, count: usize) -> Vec<T> {
    let mut placed: Vec<Option<T>> = (0..count).map(|_| None).collect();
    for (listed, one) in answered {
        placed[listed] = Some(one);
    }
    placed
        .into_iter()
        .map(|one| one.expect("a run with no failure placed every configuration"))
        .collect()
}

/// One worker's loop: claim the next item, run it, and repeat until the list is exhausted or any worker has failed. Each result is paired with its item's position. A failure sets the stop flag and is returned with its position, which [`claim_positions`] compares to pick the earliest.
fn claim_items<W, T>(
    work: &[(usize, W)],
    next: &AtomicUsize,
    stop: &AtomicBool,
    answer: &(impl Fn(&W) -> Result<T, String> + Sync),
) -> Result<Vec<(usize, T)>, (usize, String)> {
    let mut mine: Vec<(usize, T)> = Vec::new();
    while !stop.load(Ordering::Relaxed) {
        let position = next.fetch_add(1, Ordering::Relaxed);
        let Some((listed, item)) = work.get(position) else {
            break;
        };
        match answer(item) {
            Ok(answered) => mine.push((*listed, answered)),
            Err(error) => {
                stop.store(true, Ordering::Relaxed);
                return Err((*listed, error));
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

/// The wave's worklist: every configuration except the one at `default_position`, each paired with its list position, sorted by unlocking-rune count, largest first, with list position as the tie-break.
///
/// The count estimates how long a delta traces on its own: a delta whose features unlock more runes can use less of `default`'s memo and enumerates more itself. The estimate only has to separate the memo-heavy deltas from the cheap ones. Starting the heavy ones first ends the wave sooner when the machine runs fewer slots than there are deltas, because a heavy delta started last would run alone while the other slots sit idle. The order cannot change the output: each delta reads only `default`'s snapshot and the previous build's files, never another delta's output, and each result is placed by its list position.
fn delta_worklist<'c>(
    index: &SpecIndex,
    configs: &'c [Configuration<'c>],
    default_position: usize,
) -> Vec<(usize, DeltaWork<'c>)> {
    let mut work: Vec<(usize, DeltaWork<'c>)> = configs
        .iter()
        .enumerate()
        .filter(|(listed, _)| *listed != default_position)
        .map(|(listed, config)| {
            let unlocking = unlocking_runes(index, &config.features);
            (listed, DeltaWork { config, unlocking })
        })
        .collect();
    work.sort_by_key(|(listed, work)| (Reverse(work.unlocking.len()), *listed));
    work
}

/// One configuration's table build result: the digest [`artifacts::table_digest`] computes over its decision and join tables, and its timing lines.
#[derive(Debug)]
pub struct TableAnswer {
    pub digest: String,
    pub timed: Vec<String>,
}

/// How many exchange rounds a table build allows before it gives up. Each round that does not end the exchange imports at least one window that no configuration held before it, so the bound only catches a fault in the exchange itself.
const EXCHANGE_ROUNDS: usize = 64;

/// Builds every configuration's settlement table, join table, window file, and digest under `outdir`, running at most `workers` enumerations or folds at once. Each configuration reads the memos `sharing` allows and writes the memo file `sharing` asks for.
///
/// Every configuration runs on a thread of its own from its enumeration to its files, because its product and window options hold `Rc`s and cannot cross threads (`EnumeratedTables`). The threads meet on a `Board`, which grants `workers` slots in ticket order and holds the rendezvous of the exchange:
///
/// - Each configuration enumerates, then prepares its fold ([`fold::Prepared`]), folds its rules from its own rows, and publishes them in canonical labels ([`crossconfig::SharedRules`]), all inside one slot.
/// - Once every configuration has published, the exchange runs in rounds ([`crossconfig`]). In each, every configuration evaluates every configuration's rules over its own rows, for the inputs any configuration refolded in the last round, and leaves each the rows it answers wrongly where the shipped order lets that answer fire; then every configuration takes in what it was left, refolds the inputs that gained rows, and publishes again. The rounds end when no configuration took in anything. The exchange holds no slot: what a configuration holds through it is its parked product, expansion and row chains, and the evaluation adds little.
/// - Each configuration then finishes its fold inside a slot: the partition replay over its own and its imported rows, the certificates, the three artifact files, and the digest.
///
/// When `default_memo_sharing` is on and the set has the no-feature configuration and at least one other, `default` enumerates first and alone on the calling thread and keeps its memo. The calling thread then continues as `default`'s thread, whose preparation takes the first slot, while each delta takes a slot in [`delta_worklist`] order and reads `default`'s memo behind an exclusion of its own unlocking runes; `default`'s memo is freed when the last delta has enumerated. A delta also reads `default`'s previous memo file behind its unlocking runes, the edited runes, and the moved classes, and reads its own previous file only for the windows that name one of its unlocking runes, which are the windows `default`'s memo cannot answer for it. Otherwise there is no in-process memo to share, every configuration enumerates from scratch, takes its slot in list order, reads only its own previous file, and writes its memo file ahead of its drain.
///
/// Where the memo files of a build that shares `default`'s memo are written depends on `overlap_memo_writes`:
///
/// - Off, `default` writes its memo file inside its enumeration, so the write finishes before any delta starts and the rows the writer holds for the largest memo are never resident at the same time as a delta at its peak. Each delta writes its own memo file at its fixpoint's release point, so it holds no memo through its drain, sort, or fold.
/// - On, every configuration hands its memo file at its release point to a writer thread of its own, which takes no slot and which the configuration's thread joins once its files are written. `default`'s write runs beside its own drain and sort and the start of the wave, so the wave does not wait for it, and each delta's beside its drain, sort and fold, which holds the delta's snapshot until the write ends. `kernel_exec.MEMO_WRITE_OVERLAP_BYTES` books what the writers hold beside the wave.
///
/// Every previous memo is loaded without the keys that name edited runes, since those entries cannot answer a lookup or be carried into the written memo. The exclusions still check the remaining entries' reads for edited runes and moved classes at lookup and when writing.
///
/// The output does not depend on the width or on which thread finishes first: each step of the exchange reads only what every configuration published before the last rendezvous, a receiver takes its rows in list order of their sources, and results, digests and `[t]` lines are placed by list position. A configuration that fails before the exchange ends halts the board, which releases every thread waiting on it; one that fails while it finishes halts nothing, since no other configuration waits on it then, so every configuration still finishes. The error reported is the earliest-listed configuration's own among those that failed, never a halt.
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
    sharing: MemoSharing,
) -> Result<Vec<TableAnswer>, String> {
    std::fs::create_dir_all(outdir).map_err(|error| format!("{}: {error}", outdir.display()))?;
    let mode_token = modes.token();
    let edited = Exclusion::of(index, sharing.edited.iter().copied())
        .with_classes(sharing.moved_classes.iter().copied());
    let spellings = Spellings::new(
        index,
        configs
            .iter()
            .map(|config| (config.token, config.features.as_slice())),
    );
    let board = Board::new(configs.len(), workers.max(1));
    let run = TableRun {
        index,
        outdir,
        inputs,
        report,
        spellings: &spellings,
        board: &board,
    };
    let default_position = sharing
        .default_memo_sharing
        .then(|| configs.iter().position(|config| config.features.is_empty()))
        .flatten()
        .filter(|_| configs.len() > 1);
    let Some(default_position) = default_position else {
        let ended: Vec<(usize, Result<TableAnswer, Stop>)> = std::thread::scope(|scope| {
            let handles: Vec<_> = configs
                .iter()
                .enumerate()
                .map(|(listed, config)| {
                    let (run, sharing, edited, mode_token) = (&run, &sharing, &edited, &mode_token);
                    scope.spawn(move || {
                        let answer = table_thread(run, listed, config.token, listed, || {
                            let previous = load_previous_memo(
                                index,
                                sharing,
                                config.token,
                                mode_token,
                                |key| !edited.names(key),
                            )?;
                            let access = MemoAccess {
                                shared_memos: shared_behind(previous.as_ref(), edited.clone())
                                    .into_iter()
                                    .collect(),
                                keep_memo: false,
                            };
                            let carried = shared_behind(previous.as_ref(), edited.clone())
                                .into_iter()
                                .collect();
                            let file =
                                memo_file(sharing, outdir, config.token, mode_token, carried);
                            enumerate_config_tables(
                                index, config, modes, report, access, file, None,
                            )
                        });
                        (listed, answer)
                    })
                })
                .collect();
            joined(handles)
        });
        return settle_ends(ended, configs.len());
    };
    let default = &configs[default_position];
    let previous_default = load_previous_memo(index, &sharing, default.token, &mode_token, |key| {
        !edited.names(key)
    })
    .map_err(|error| format!("{}: {error}", default.token))?;
    let rest = delta_worklist(index, configs, default_position);
    let (run, rest, sharing, edited, mode_token) = (&run, &rest, &sharing, &edited, &mode_token);
    let ended = std::thread::scope(move |scope| {
        let beside = sharing.overlap_memo_writes.then_some(scope);
        let pending = enumerate_config_tables(
            index,
            default,
            modes,
            report,
            MemoAccess {
                shared_memos: shared_behind(previous_default.as_ref(), edited.clone())
                    .into_iter()
                    .collect(),
                keep_memo: true,
            },
            memo_file(
                sharing,
                outdir,
                default.token,
                mode_token,
                shared_behind(previous_default.as_ref(), edited.clone())
                    .into_iter()
                    .collect(),
            ),
            beside,
        )
        .map_err(|error| format!("{}: {error}", default.token))?;
        let memo: Arc<MemoSnapshot> = Arc::clone(
            pending
                .memo
                .as_ref()
                .expect("a kept memo comes back from a trace-memo enumeration"),
        );
        let handles: Vec<_> = rest
            .iter()
            .enumerate()
            .map(|(position, (listed, work))| {
                let memo = Arc::clone(&memo);
                let previous_default = previous_default.clone();
                let listed = *listed;
                scope.spawn(move || {
                    let config = work.config;
                    let answer = table_thread(run, listed, config.token, position + 1, || {
                        let behind_unlocking = Exclusion::of(index, work.unlocking.iter().copied());
                        let previous_own =
                            load_previous_memo(index, sharing, config.token, mode_token, |key| {
                                behind_unlocking.names(key) && !edited.names(key)
                            })?;
                        let mut shared = vec![SharedMemo {
                            memo,
                            excluded: behind_unlocking,
                        }];
                        shared.extend(shared_behind(
                            previous_default.as_ref(),
                            Exclusion::of(
                                index,
                                work.unlocking.iter().chain(edited.runes()).copied(),
                            )
                            .with_classes(edited.classes().iter().copied()),
                        ));
                        drop(previous_default);
                        shared.extend(shared_behind(previous_own.as_ref(), edited.clone()));
                        let carried = shared_behind(previous_own.as_ref(), edited.clone())
                            .into_iter()
                            .collect();
                        let file = memo_file(sharing, outdir, config.token, mode_token, carried);
                        let access = MemoAccess {
                            shared_memos: shared,
                            keep_memo: false,
                        };
                        enumerate_config_tables(index, config, modes, report, access, file, beside)
                    });
                    (listed, answer)
                })
            })
            .collect();
        drop(memo);
        drop(previous_default);
        let lead = table_thread(run, default_position, default.token, 0, move || Ok(pending));
        let mut ended = joined(handles);
        ended.push((default_position, lead));
        Ok::<_, String>(ended)
    })?;
    settle_ends(ended, configs.len())
}

/// What every thread of one table build reads: the spec, where to write, the report flags, the build's spellings, and the board.
struct TableRun<'a> {
    index: &'a SpecIndex,
    outdir: &'a Path,
    inputs: &'a str,
    report: Report,
    spellings: &'a Spellings,
    board: &'a Board,
}

/// Why a configuration's thread ended without its tables: its own failure, with the message the run reports, or a halt another configuration's failure caused while this thread waited.
enum Stop {
    Failed(String),
    Halted,
}

/// The slots, the rendezvous and the mailboxes the threads of one table build share ([`run_configs_tables`]).
///
/// A slot bounds how many configurations enumerate, prepare or finish at once. A thread asks for its first slot with a ticket, and slots go to tickets in order, so the deltas start heaviest first. The rendezvous holds every thread until all have arrived. Each configuration publishes its rules there with the canonical inputs it refolded last (`None` for every input), and each source leaves each receiver its rows. A halt wakes every waiting thread with [`Stop::Halted`].
struct Board {
    state: Mutex<BoardState>,
    turn: Condvar,
    count: usize,
}

/// The canonical inputs a configuration refolded in the last round, shared by every source that re-evaluates them.
type Refolded = Arc<HashSet<Box<str>>>;

struct BoardState {
    free: usize,
    ticket: usize,
    arrived: usize,
    generation: u64,
    halted: bool,
    published: Vec<Arc<SharedRules>>,
    refolded: Vec<Option<Refolded>>,
    mail: Vec<Vec<Vec<ForeignRow>>>,
}

impl Board {
    fn new(count: usize, slots: usize) -> Self {
        Self {
            state: Mutex::new(BoardState {
                free: slots,
                ticket: 0,
                arrived: 0,
                generation: 0,
                halted: false,
                published: (0..count).map(|_| Arc::default()).collect(),
                refolded: (0..count).map(|_| None).collect(),
                mail: (0..count)
                    .map(|_| (0..count).map(|_| Vec::new()).collect())
                    .collect(),
            }),
            turn: Condvar::new(),
            count,
        }
    }

    fn lock(&self) -> MutexGuard<'_, BoardState> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn wait<'s>(&self, state: MutexGuard<'s, BoardState>) -> MutexGuard<'s, BoardState> {
        self.turn
            .wait(state)
            .unwrap_or_else(PoisonError::into_inner)
    }

    /// Waits for the slot that ticket `ticket` is due.
    fn take_turn(&self, ticket: usize) -> Result<(), Stop> {
        let mut state = self.lock();
        loop {
            if state.halted {
                return Err(Stop::Halted);
            }
            if state.ticket == ticket && state.free > 0 {
                state.free -= 1;
                state.ticket += 1;
                self.turn.notify_all();
                return Ok(());
            }
            state = self.wait(state);
        }
    }

    /// Waits for any free slot.
    fn take_slot(&self) -> Result<(), Stop> {
        let mut state = self.lock();
        loop {
            if state.halted {
                return Err(Stop::Halted);
            }
            if state.free > 0 {
                state.free -= 1;
                return Ok(());
            }
            state = self.wait(state);
        }
    }

    fn give_slot(&self) {
        self.lock().free += 1;
        self.turn.notify_all();
    }

    /// Waits until every configuration has arrived.
    fn meet(&self) -> Result<(), Stop> {
        let mut state = self.lock();
        if state.halted {
            return Err(Stop::Halted);
        }
        state.arrived += 1;
        let generation = state.generation;
        if state.arrived == self.count {
            state.arrived = 0;
            state.generation += 1;
            self.turn.notify_all();
            return Ok(());
        }
        while state.generation == generation {
            if state.halted {
                return Err(Stop::Halted);
            }
            state = self.wait(state);
        }
        Ok(())
    }

    fn halt(&self) {
        self.lock().halted = true;
        self.turn.notify_all();
    }

    fn publish(
        &self,
        config: usize,
        rules: Option<SharedRules>,
        refolded: Option<HashSet<Box<str>>>,
    ) {
        let mut state = self.lock();
        if let Some(rules) = rules {
            state.published[config] = Arc::new(rules);
        }
        state.refolded[config] = refolded.map(Arc::new);
    }

    fn rules_of(&self, config: usize) -> (Arc<SharedRules>, Option<Refolded>) {
        let state = self.lock();
        (
            Arc::clone(&state.published[config]),
            state.refolded[config].clone(),
        )
    }

    fn deliver(&self, receiver: usize, source: usize, rows: Vec<ForeignRow>) {
        self.lock().mail[receiver][source] = rows;
    }

    fn collect(&self, receiver: usize) -> Vec<Vec<ForeignRow>> {
        self.lock().mail[receiver]
            .iter_mut()
            .map(std::mem::take)
            .collect()
    }

    /// Whether the last round refolded nothing anywhere.
    fn settled(&self) -> bool {
        self.lock()
            .refolded
            .iter()
            .all(|refolded| refolded.as_ref().is_some_and(|inputs| inputs.is_empty()))
    }
}

/// Halts the board when a configuration's thread ends without disarming it, by an error or by a panic, so no other thread waits on it for ever.
struct Watch<'b> {
    board: &'b Board,
    armed: bool,
}

impl Drop for Watch<'_> {
    fn drop(&mut self) {
        if self.armed {
            self.board.halt();
        }
    }
}

/// Joins the threads, resuming the first panic once every thread has ended.
fn joined<T>(handles: Vec<std::thread::ScopedJoinHandle<'_, T>>) -> Vec<T> {
    let mut ended: Vec<T> = Vec::with_capacity(handles.len());
    let mut panicked = None;
    for handle in handles {
        match handle.join() {
            Ok(value) => ended.push(value),
            Err(panic) => {
                panicked.get_or_insert(panic);
            }
        }
    }
    if let Some(panic) = panicked {
        std::panic::resume_unwind(panic);
    }
    ended
}

/// The run's answer from every thread's end: the earliest-listed configuration's own failure when there is one, and otherwise every configuration's tables in list order.
fn settle_ends(
    ended: Vec<(usize, Result<TableAnswer, Stop>)>,
    count: usize,
) -> Result<Vec<TableAnswer>, String> {
    let mut answered: Vec<(usize, TableAnswer)> = Vec::with_capacity(count);
    let mut failure: Option<(usize, String)> = None;
    let mut halted = false;
    for (listed, end) in ended {
        match end {
            Ok(answer) => answered.push((listed, answer)),
            Err(Stop::Failed(error)) => {
                if failure.as_ref().is_none_or(|(worst, _)| listed < *worst) {
                    failure = Some((listed, error));
                }
            }
            Err(Stop::Halted) => halted = true,
        }
    }
    match failure {
        Some((_, error)) => Err(error),
        None if halted => Err("the table build halted without a failure to report".to_owned()),
        None => Ok(place_answers(answered, count)),
    }
}

/// One configuration's table build on its own thread, from its enumeration to its files, as [`run_configs_tables`] describes. `enumerate` runs inside the slot `ticket` is due. A memo writer the enumeration started beside it is joined once the files are written, outside any slot, and a write that failed fails the configuration. When asked, the timing lines are the enumeration's `enumerate[<config>]` and `memo[<config>]` after the cache stats' `[c]` lines, the second the writer's own time when it ran beside, then `fold.prefixes[<config>]`, then, with the cache stats, `[c] <config> resident_parked`, the process's resident size once every configuration has parked its prepared product and before the exchange's first round, and on the first-listed configuration's thread `[c] <config> parked_heap`, the bytes the process's malloc zones hold allocated at that point ([`crate::fixpoint::heap_bytes`]), which every other thread waits for before the exchange starts and which `kernel_exec.PARKED_FOLD_BYTES` is set from, then `fold.exchange[<config>]` (the time this configuration spent evaluating, taking in and refolding, without the waits), and `fold.partition[<config>]` at millisecond precision, then `fold[<config>]`, which covers the preparation and the finish.
fn table_thread<'a, 's>(
    run: &TableRun<'a>,
    listed: usize,
    token: &str,
    ticket: usize,
    enumerate: impl FnOnce() -> Result<EnumeratedTables<'a, 's>, String>,
) -> Result<TableAnswer, Stop> {
    let mut watch = Watch {
        board: run.board,
        armed: true,
    };
    let fail = |error: String| Stop::Failed(format!("{token}: {error}"));
    let timings = run.report.timings;
    run.board.take_turn(ticket)?;
    let EnumeratedTables {
        product,
        mut options,
        memo,
        mut timed,
        writer,
    } = enumerate().map_err(fail)?;
    drop(memo);
    let memo_line_at = timed.len();
    let started = Instant::now();
    let mut fold_lines: Vec<String> = Vec::new();
    let prepared = if timings {
        fold::Prepared::new(
            product,
            Some(&mut |phase: &str, elapsed: Duration| {
                fold_lines.push(fold_timing_line(&format!("fold.{phase}[{token}]"), elapsed));
            }),
        )
    } else {
        fold::Prepared::new(product, None)
    }
    .map_err(fail)?;
    timed.append(&mut fold_lines);
    let mut imports = Imports::default();
    let mut folds = prepared.unfolded();
    prepared
        .fold_inputs(run.index, &imports, None, &mut folds)
        .map_err(fail)?;
    let indexed = SourceRows::of(&prepared.rows());
    run.board.publish(
        listed,
        Some(SharedRules::of(
            run.spellings,
            listed,
            &fold::Prepared::rules(&folds),
        )),
        None,
    );
    let mut folding = started.elapsed();
    run.board.give_slot();

    let mut exchanging = Duration::ZERO;
    run.board.meet()?;
    if run.report.cache_stats {
        timed.push(format!(
            "[c] {token} resident_parked kb={}",
            crate::fixpoint::resident_kb()
        ));
        if listed == 0 {
            timed.push(format!(
                "[c] {token} parked_heap bytes={}",
                crate::fixpoint::heap_bytes()
            ));
        }
        run.board.meet()?;
    }
    for round in 0.. {
        if round == EXCHANGE_ROUNDS {
            return Err(fail(format!(
                "the cross-configuration exchange still moved windows after {EXCHANGE_ROUNDS} rounds"
            )));
        }
        let started = Instant::now();
        let mut everyone: Vec<Arc<SharedRules>> = Vec::with_capacity(run.board.count);
        let mut inputs: Option<HashSet<Box<str>>> = Some(HashSet::default());
        for config in 0..run.board.count {
            let (rules, refolded) = run.board.rules_of(config);
            everyone.push(rules);
            inputs = match (inputs, refolded) {
                (Some(mut held), Some(more)) => {
                    held.extend(more.iter().cloned());
                    Some(held)
                }
                _ => None,
            };
        }
        if inputs.as_ref().is_none_or(|inputs| !inputs.is_empty()) {
            let published: Vec<&SharedRules> = everyone.iter().map(|rules| &**rules).collect();
            let source = Source {
                index: run.index,
                spellings: run.spellings,
                config: listed,
                rows: prepared.rows(),
                indexed: &indexed,
                chains: prepared.chains(),
                published: &published,
            };
            let sent = crossconfig::exports(&source, inputs.as_ref());
            for (receiver, rows) in sent.into_iter().enumerate() {
                if receiver != listed {
                    run.board.deliver(receiver, listed, rows);
                }
            }
        }
        exchanging += started.elapsed();
        run.board.meet()?;
        let started = Instant::now();
        let batches = run.board.collect(listed);
        let gained = imports
            .absorb(run.spellings, listed, &prepared.rows(), batches)
            .map_err(fail)?;
        let refolded: HashSet<Box<str>> = gained
            .iter()
            .map(|input| Box::from(&*run.spellings.canonical(listed, input)))
            .collect();
        if gained.is_empty() {
            run.board.publish(listed, None, Some(refolded));
        } else {
            let wanted: HashSet<Rc<str>> = gained.into_iter().collect();
            prepared
                .fold_inputs(run.index, &imports, Some(&wanted), &mut folds)
                .map_err(fail)?;
            run.board.publish(
                listed,
                Some(SharedRules::of(
                    run.spellings,
                    listed,
                    &fold::Prepared::rules(&folds),
                )),
                Some(refolded),
            );
        }
        exchanging += started.elapsed();
        run.board.meet()?;
        if run.board.settled() {
            break;
        }
    }
    if timings {
        timed.push(fold_timing_line(
            &format!("fold.exchange[{token}]"),
            exchanging,
        ));
    }

    run.board.take_slot()?;
    let started = Instant::now();
    let mut fold_lines: Vec<String> = Vec::new();
    let mut reporter = |phase: &str, elapsed: Duration| {
        fold_lines.push(fold_timing_line(&format!("fold.{phase}[{token}]"), elapsed));
    };
    let finished = prepared
        .finish(
            run.index,
            &mut options,
            folds,
            &imports,
            Some((run.spellings, listed)),
            if timings { Some(&mut reporter) } else { None },
        )
        .and_then(|folded| write_tables(run, token, &folded));
    run.board.give_slot();
    watch.armed = false;
    if let Some(writer) = writer {
        let wrote = writer
            .join()
            .unwrap_or_else(|panic| std::panic::resume_unwind(panic))
            .map_err(fail)?;
        if timings {
            timed.insert(memo_line_at, timing_line(&format!("memo[{token}]"), wrote));
        }
    }
    let digest = finished.map_err(fail)?;
    timed.append(&mut fold_lines);
    folding += started.elapsed();
    if timings {
        timed.push(timing_line(&format!("fold[{token}]"), folding));
    }
    Ok(TableAnswer { digest, timed })
}

/// Writes one configuration's three artifact files and returns its table digest.
fn write_tables(run: &TableRun<'_>, token: &str, folded: &fold::Folded) -> Result<String, String> {
    let settlement = run.outdir.join(format!("settlement-{token}.tsv"));
    write_text(&settlement, &artifacts::settlement_tsv(&folded.decision))?;
    let joins = run.outdir.join(format!("joins-{token}.tsv"));
    write_text(&joins, &artifacts::join_tsv(&folded.joins))?;
    let windows = run.outdir.join(format!("windows-{token}.tsv"));
    artifacts::write_windows(run.index, &folded.decision, run.inputs, &windows)
        .map_err(|error| format!("{}: {error}", windows.display()))?;
    Ok(artifacts::table_digest(
        run.index,
        &folded.decision,
        &folded.joins,
    ))
}

/// One configuration enumerated and holding what its fold needs, its memo file already written or its writer running beside it.
///
/// It cannot cross threads, because the product's label pool holds `Rc<str>` ([`crate::stream`]) and the window options hold `Rc<FollowerMap>` ([`crate::options`]). That is why each configuration of a table build runs on one thread from its enumeration to its files, and why a caller clones the memo out first: behind its [`Arc`], the memo is the only part of an enumeration that other configurations read. Only a configuration whose memo access set `keep_memo` has a memo here. A delta's memo was written to its file and dropped at the fixpoint's release point, or is held by its writer until the write ends. `writer` is that writer, which returns how long the write took.
struct EnumeratedTables<'i, 's> {
    product: FixpointProduct,
    options: WindowOptions<'i>,
    memo: Option<Arc<MemoSnapshot>>,
    timed: Vec<String>,
    writer: Option<ScopedJoinHandle<'s, Result<Duration, String>>>,
}

/// One configuration's enumeration for its tables: the fixpoint over what `access` allows, writing the memo file at the release point when one is named, and keeping the finished memo behind an [`Arc`] when `access` asks. With a `beside` scope, the file is instead handed at the release point to a writer spawned in that scope, and the writer comes back for the caller to join. The timing lines are `enumerate[<config>]`, which excludes a write in line, and for that write `memo[<config>]`, after the cache stats' `[c]` lines.
fn enumerate_config_tables<'i: 's, 's>(
    index: &'i SpecIndex,
    config: &Configuration<'_>,
    modes: EnumerationModes,
    report: Report,
    access: MemoAccess,
    file: Option<MemoFile>,
    beside: Option<&'s Scope<'s, '_>>,
) -> Result<EnumeratedTables<'i, 's>, String> {
    let token = config.token;
    let mut timed: Vec<String> = Vec::new();
    let mut stats: Vec<String> = Vec::new();
    let mut writer = None;
    let file = file.map(|file| match beside {
        None => MemoWrite::Inline(file),
        Some(scope) => MemoWrite::Beside(
            file,
            Box::new(|file: MemoFile, memo: Arc<MemoSnapshot>| {
                writer = Some(scope.spawn(move || {
                    let started = Instant::now();
                    write_memo(index, &file.path, &file.head, &memo, &file.carried)
                        .map(|()| started.elapsed())
                }));
            }),
        ),
    });
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
        report.cache_stats.then_some(&mut stats),
        access,
        file,
    )?;
    timed.append(&mut stats);
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
        memo,
        timed,
        writer,
    })
}

/// One configuration's string replay result: the walk's counts, and its cache-stats and timing lines when requested.
pub struct ReplayAnswer {
    pub report: replay::Report,
    pub timed: Vec<String>,
}

/// Replays every configuration's persisted rules over `text_set`, at most `workers` at a time. Each configuration reads `<outdir>/settlement-<config>.tsv` back, walks the text set, and checks the rules' first-match result against the engine's own settlement, window by window. The engine gets the enumeration's two engine mode flags but not `deep_classes`, since a replay settles single windows, which have no grain. With a `memo_dir`, each passing walk writes its window memo there as `replay-windows-<config>.bin` ([`replay::Replay::write_window_memo`]). A walk that fails writes nothing. A walk that released its memo under the text set's ceiling fails instead of writing, and the `replay-strings` CLI rejects `--memo-dir` with `--memo-windows`, so no command line can request that.
#[allow(clippy::too_many_arguments)]
pub fn run_configs_replay(
    index: &SpecIndex,
    configs: &[Configuration<'_>],
    modes: EnumerationModes,
    outdir: &Path,
    text_set: replay::TextSet<'_>,
    workers: usize,
    report: Report,
    memo_dir: Option<&Path>,
) -> Result<Vec<ReplayAnswer>, String> {
    claim_all(configs, workers, |config| {
        run_config_replay(index, config, modes, outdir, text_set, report, memo_dir)
            .map_err(|error| format!("{}: {error}", config.token))
    })
}

/// Where one configuration's window memo is written under `memo_dir`. `kernel_exec.replay_memo_dump` builds the same path.
pub fn replay_memo_path(memo_dir: &Path, token: &str) -> PathBuf {
    memo_dir.join(format!("replay-windows-{token}.bin"))
}

/// Replays one configuration: reads its rules back and walks its text set. When asked, it returns the cache stats' `[c]` lines and a `replay[<config>]` timing line. With a `memo_dir`, it then writes the window memo, timed as `replay_memo[<config>]`. The walk's clock stops before the cache stats are taken, so the end-of-walk resident-size sample is not counted in the walk's time; the samples a walk with cache stats takes at each release under the text set's ceiling are.
pub fn run_config_replay(
    index: &SpecIndex,
    config: &Configuration<'_>,
    modes: EnumerationModes,
    outdir: &Path,
    text_set: replay::TextSet<'_>,
    report: Report,
    memo_dir: Option<&Path>,
) -> Result<ReplayAnswer, String> {
    let token = config.token;
    let started = Instant::now();
    let settlement = outdir.join(format!("settlement-{token}.tsv"));
    let text = std::fs::read_to_string(&settlement)
        .map_err(|error| format!("{}: {error}", settlement.display()))?;
    let rules = artifacts::read_settlement_tsv(&text)
        .map_err(|error| format!("{}: {error}", settlement.display()))?;
    let engine_modes = EngineModes {
        simulated_prospect: modes.simulated_prospect,
        follower_prefer_slots: modes.follower_prefer_slots,
        ..EngineModes::default()
    };
    let mut walk = replay::Replay::new(index, config.features.clone(), engine_modes, &rules);
    if report.cache_stats {
        walk.with_cache_stats(token);
    }
    let walked = walk.walk_texts(text_set)?;
    let elapsed = started.elapsed();
    let mut timed = walk.take_cache_stats();
    if report.timings {
        timed.push(timing_line(&format!("replay[{token}]"), elapsed));
    }
    if let Some(memo_dir) = memo_dir {
        let started = Instant::now();
        walk.write_window_memo(
            &replay_memo_path(memo_dir, token),
            token,
            text_set.max_length,
        )?;
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
        Failure::Refused(error) => format!("{}: {error}", config.token),
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

    /// The default modes (the [`EnumerationModes`] default), under which the deep slots enumerate at class grain. The tests below check byte-identity across schedules under these modes.
    const DEFAULT_MODES: EnumerationModes = EnumerationModes {
        simulated_prospect: true,
        follower_prefer_slots: true,
        deep_classes: true,
    };

    /// Two configurations the fixture tells apart: `ss03` unlocks a `qsMay` entry, and `default` unlocks nothing.
    const TOKENS: [&str; 2] = ["default", "ss03"];

    /// A set whose claim order differs from its listed order: `default`; `ss09`, a delta that unlocks nothing; and `ss03`, the delta that unlocks `qsMay`, which the wave claims first although it is listed last. `ss09` has no features because the fixture declares no second feature. It still runs as a delta, because only the first no-feature configuration shares its memo.
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
        run_config(
            index,
            config,
            DEFAULT_MODES,
            &mut sink,
            Report::timed(false),
        )
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
                DEFAULT_MODES,
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
                DEFAULT_MODES,
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
        let timed = run_configs(
            &index,
            &configs,
            DEFAULT_MODES,
            &outdir,
            2,
            Report::timed(false),
        )
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
        let error = run_configs(
            &index,
            &configs,
            DEFAULT_MODES,
            &outdir,
            1,
            Report::timed(false),
        )
        .expect_err("a directory in a stream's place is not writable");
        assert!(
            error.starts_with(&format!("{}: ", TOKENS[0])),
            "the error names the configuration that failed: {error}"
        );
        assert!(
            error.contains(&format!("transitions-{}.ndjson", TOKENS[0])),
            "and the file it failed on: {error}"
        );
        std::fs::remove_dir_all(&outdir).expect("the scratch directory is removable");
    }

    /// With every stream path blocked and one worker per configuration, the run reports the error at the earliest list position, whichever worker reached it and however many others also failed. Position 0 is always claimed: the first claim takes it, and a worker stops claiming only after some worker has failed.
    #[test]
    fn the_error_a_run_reports_is_the_earliest_listed_one() {
        let index = fixtures::mini();
        let configs = configurations(&index);
        let outdir = scratch("fan-out-all-blocked");
        std::fs::create_dir_all(&outdir).expect("the scratch directory is makeable");
        for token in TOKENS {
            block(&outdir, token);
        }
        let error = run_configs(
            &index,
            &configs,
            DEFAULT_MODES,
            &outdir,
            configs.len(),
            Report::timed(false),
        )
        .expect_err("no configuration can write its stream");
        assert!(
            error.starts_with(&format!("{}: ", TOKENS[0])),
            "the earliest configuration is the one reported: {error}"
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
        run_configs(
            &index,
            &configs,
            DEFAULT_MODES,
            &outdir,
            2,
            Report::timed(false),
        )
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
        let timed = run_configs(
            &index,
            &configs,
            DEFAULT_MODES,
            &outdir,
            0,
            Report::timed(false),
        )
        .expect("the run happens");
        assert_eq!(timed.len(), configs.len());
        for config in &configs {
            assert!(transitions_path(&outdir, config.token).exists());
        }
        std::fs::remove_dir_all(&outdir).expect("the scratch directory is removable");
    }

    /// The inputs stamp every table build below writes its window files under.
    const INPUTS: &str = "fixture-stamp";

    /// Memo sharing that writes memo files, so every configuration writes one at its release point and `default` writes its file before the wave reads its snapshot.
    fn stamped() -> MemoSharing {
        MemoSharing {
            memo_stamp: Some("identity".to_owned()),
            ..MemoSharing::default()
        }
    }

    /// [`stamped`] with every memo file handed at its release point to a writer beside the rest of the build.
    fn overlapped() -> MemoSharing {
        MemoSharing {
            overlap_memo_writes: true,
            ..stamped()
        }
    }

    /// What one width of the table fan-out wrote: every configuration's digest, and every configuration's files as [`table_files`] reads them.
    type Filed = (Vec<String>, Vec<Vec<(String, Vec<u8>)>>);

    /// The files one configuration's table build writes, read back as bytes: the three tables, plus the memo file when the build had a stamp.
    fn table_files(outdir: &Path, token: &str, memo: bool) -> Vec<(String, Vec<u8>)> {
        let mut names: Vec<String> = ["settlement", "joins", "windows"]
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

    /// The table fan-out writes the same bytes at every width, over the set whose claim order differs from its listed order. Tables, memo files, and digests match per configuration at 0, 1, 2, and 8 workers. At 2, one worker takes the heavy delta and the lead claims the cheap one after `default`'s fold, which is the case the claim order is for. At 8, the deltas enumerate while `default`'s fold runs on the calling thread. In the stamped case, every configuration writes its memo file at its release point, and those files are compared too. The stamped case's tables and digests must also match the unstamped case's at the same width, which shows that writing the memo file changes no table. In the overlapped case, every memo file is written beside the rest of the build, `default`'s beside the wave, and every file and digest must match the stamped case's at the same width.
    #[test]
    fn a_table_fan_out_files_the_same_bytes_at_every_width() {
        let index = fixtures::mini();
        let configs = permuting(&index);
        let root = scratch("fan-out-tables");
        let mut unstamped: Vec<Filed> = Vec::new();
        let mut in_line: Vec<Filed> = Vec::new();
        for (arm, sharing) in [
            ("unstamped", MemoSharing::default()),
            ("stamped", stamped()),
            ("overlapped", overlapped()),
        ] {
            let with_memo = sharing.memo_stamp.is_some();
            let mut first: Option<Filed> = None;
            for (width, workers) in [0, 1, 2, 8].into_iter().enumerate() {
                let outdir = root.join(arm).join(format!("at-{workers}"));
                let answers = run_configs_tables(
                    &index,
                    &configs,
                    DEFAULT_MODES,
                    &outdir,
                    INPUTS,
                    workers,
                    Report::timed(false),
                    sharing.clone(),
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
                if sharing.overlap_memo_writes {
                    let (written_digests, written_files) = in_line.get(width).expect(
                        "the stamped case writes every width before the overlapped one runs",
                    );
                    assert_eq!(
                        &digests, written_digests,
                        "overlapped at {workers} workers: the digests the stamped case wrote"
                    );
                    same_files(
                        &files,
                        written_files,
                        &format!("overlapped at {workers} workers"),
                        "the stamped case",
                    );
                    continue;
                }
                let (plain_digests, plain_files) = unstamped
                    .get(width)
                    .expect("the unstamped case writes every width before the stamped one runs");
                assert_eq!(
                    &digests, plain_digests,
                    "stamped at {workers} workers: the digests the unstamped case wrote"
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
                    "the unstamped case",
                );
                in_line.push((digests, files));
            }
        }
        std::fs::remove_dir_all(&root).expect("the scratch directory is removable");
    }

    /// A table build's timing lines come back in the caller's configuration order, with each configuration's phases in the order they ran, at any width and in either memo-write order. `default`'s fold lines follow its enumerate and memo lines even when its preparation ran alongside the deltas, a memo line stays next to its enumerate line when the write ran beside the fold, and `ss03`'s lines come last although its slot comes first among the deltas.
    #[test]
    fn a_table_build_times_its_phases_in_configuration_order_at_any_width() {
        let index = fixtures::mini();
        let configs = permuting(&index);
        let root = scratch("fan-out-tables-timings");
        for (arm, sharing, workers) in [1, 2, 8].into_iter().flat_map(|workers| {
            [
                ("stamped", stamped(), workers),
                ("overlapped", overlapped(), workers),
            ]
        }) {
            let outdir = root.join(arm).join(format!("at-{workers}"));
            let answers = run_configs_tables(
                &index,
                &configs,
                DEFAULT_MODES,
                &outdir,
                INPUTS,
                workers,
                Report::timed(true),
                sharing,
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
                    "fold.exchange[default]",
                    "fold.partition[default]",
                    "fold[default]",
                    "enumerate[ss09]",
                    "memo[ss09]",
                    "fold.prefixes[ss09]",
                    "fold.exchange[ss09]",
                    "fold.partition[ss09]",
                    "fold[ss09]",
                    "enumerate[ss03]",
                    "memo[ss03]",
                    "fold.prefixes[ss03]",
                    "fold.exchange[ss03]",
                    "fold.partition[ss03]",
                    "fold[ss03]"
                ],
                "{arm} at {workers} workers"
            );
        }
        std::fs::remove_dir_all(&root).expect("the scratch directory is removable");
    }

    /// A memo file written beside the build that cannot be written fails its configuration once the configuration's files are written, and the run reports that failure: with a directory in `default`'s memo path, the overlapped build reports `default`'s error naming the file, while `default`'s tables and the delta's tables and memo file are all filed.
    #[test]
    fn a_memo_write_beside_the_build_that_fails_is_what_the_run_reports() {
        let index = fixtures::mini();
        let configs = configurations(&index);
        let outdir = scratch("fan-out-tables-memo-blocked");
        std::fs::create_dir_all(memo_path(&outdir, TOKENS[0]))
            .expect("a directory can occupy a memo file's path");
        let error = run_configs_tables(
            &index,
            &configs,
            DEFAULT_MODES,
            &outdir,
            INPUTS,
            2,
            Report::timed(false),
            overlapped(),
        )
        .expect_err("a directory in the memo file's place is not writable");
        assert!(
            error.starts_with(&format!("{}: ", TOKENS[0])),
            "the error names the configuration that failed: {error}"
        );
        assert!(
            error.contains(&format!("memo-{}.tsv", TOKENS[0])),
            "and the file it failed on: {error}"
        );
        table_files(&outdir, TOKENS[0], false);
        table_files(&outdir, TOKENS[1], true);
        std::fs::remove_dir_all(&outdir).expect("the scratch directory is removable");
    }

    /// If `default`'s second half fails while the wave runs, the run reports `default`'s error. When only `default` is blocked, the error names the file it failed on. When every configuration is blocked, the run reports `default`'s error instead of the delta's, whether or not the delta was claimed before the stop. `a_lead_runs_beside_the_worker_that_claims_while_it_does` checks that the lead overlaps the workers; this test checks only which error the run returns.
    #[test]
    fn a_default_that_fails_during_the_wave_is_what_the_run_reports() {
        let index = fixtures::mini();
        let configs = configurations(&index);
        let root = scratch("fan-out-tables-blocked");
        let alone = root.join("default-alone");
        block_settlement(&alone, TOKENS[0]);
        let error = run_configs_tables(
            &index,
            &configs,
            DEFAULT_MODES,
            &alone,
            INPUTS,
            2,
            Report::timed(false),
            MemoSharing::default(),
        )
        .expect_err("a directory in the settlement table's place is not writable");
        assert!(
            error.starts_with(&format!("{}: ", TOKENS[0])),
            "the error names the configuration that failed: {error}"
        );
        assert!(
            error.contains(&format!("settlement-{}.tsv", TOKENS[0])),
            "and the file it failed on: {error}"
        );
        let every = root.join("every-configuration");
        for token in TOKENS {
            block_settlement(&every, token);
        }
        let error = run_configs_tables(
            &index,
            &configs,
            DEFAULT_MODES,
            &every,
            INPUTS,
            2,
            Report::timed(false),
            MemoSharing::default(),
        )
        .expect_err("no configuration can write its settlement table");
        assert!(
            error.starts_with(&format!("{}: ", TOKENS[0])),
            "default's word wins over a delta that failed beside it: {error}"
        );
        std::fs::remove_dir_all(&root).expect("the scratch directory is removable");
    }

    /// The wave's worklist over the permuting set: `ss03`, the delta that unlocks `qsMay`, comes first with position 2, and the delta that unlocks nothing follows with position 1. `default` is not in it, and each entry holds its unlocking runes.
    #[test]
    fn the_delta_worklist_is_claimed_heaviest_first() {
        let index = fixtures::mini();
        let configs = permuting(&index);
        let work = delta_worklist(&index, &configs, 0);
        let placed: Vec<(usize, &str)> = work
            .iter()
            .map(|(listed, work)| (*listed, work.config.token))
            .collect();
        assert_eq!(placed, [(2, "ss03"), (1, "ss09")]);
        let may = fixtures::sym(&index, "qsMay");
        assert_eq!(work[0].1.unlocking.len(), 1);
        assert!(work[0].1.unlocking.contains(&may));
        assert!(work[1].1.unlocking.is_empty());
    }

    /// A failing item's error is reported at the position the item carries, not the order it was claimed in. The test uses a barrier instead of relying on timing: over a worklist listed out of order, each item fails only after both items reach the barrier, so both are claimed before either failure stops claiming, whichever of the three workers claims them. The run reports the position-1 item's error although the position-3 item was claimed first.
    #[test]
    fn a_failing_item_is_reported_at_the_position_it_carries() {
        let work = [(3, "ss03"), (1, "ss09")];
        let met = std::sync::Barrier::new(2);
        let error = claim_positions(&work, 3, |token: &&str| {
            met.wait();
            Err::<(), _>(format!("{token}: blocked"))
        })
        .expect_err("both items fail");
        assert_eq!(error, "ss09: blocked");
    }

    /// The board's slots go to tickets in order and never more than its width at once: three threads asking with tickets 2, 1 and 0 on a one-slot board hold it in ticket order, each releasing before the next takes it.
    #[test]
    fn the_board_grants_its_slots_in_ticket_order_and_no_wider() {
        let board = Board::new(3, 1);
        let held = std::sync::Mutex::new(Vec::new());
        let inside = AtomicUsize::new(0);
        std::thread::scope(|scope| {
            for ticket in [2, 1, 0] {
                let (board, held, inside) = (&board, &held, &inside);
                scope.spawn(move || {
                    board
                        .take_turn(ticket)
                        .ok()
                        .expect("nothing halts the board");
                    assert_eq!(inside.fetch_add(1, Ordering::SeqCst), 0, "one slot");
                    held.lock().expect("unpoisoned").push(ticket);
                    std::thread::sleep(Duration::from_millis(5));
                    inside.fetch_sub(1, Ordering::SeqCst);
                    board.give_slot();
                });
            }
        });
        assert_eq!(*held.lock().expect("unpoisoned"), [0, 1, 2]);
    }

    /// A thread that ends without its tables, by an error or a panic, halts the board through its watch, so the threads waiting at the rendezvous or for a slot return instead of hanging, and the run reports the earliest-listed configuration's own failure, never the halt the others saw.
    #[test]
    fn a_failure_releases_every_waiting_thread_and_is_what_the_run_reports() {
        let board = Board::new(3, 1);
        let ended: Vec<(usize, Result<TableAnswer, Stop>)> = std::thread::scope(|scope| {
            let board = &board;
            let waiting = scope.spawn(move || {
                let mut watch = Watch { board, armed: true };
                let met = board.meet();
                watch.armed = met.is_ok();
                (
                    0,
                    met.map(|()| TableAnswer {
                        digest: String::new(),
                        timed: Vec::new(),
                    }),
                )
            });
            let queued = scope.spawn(move || {
                let mut watch = Watch { board, armed: true };
                board.take_slot().ok().expect("the slot is free");
                let again = board.take_slot();
                watch.armed = again.is_ok();
                (
                    1,
                    again.map(|()| TableAnswer {
                        digest: String::new(),
                        timed: Vec::new(),
                    }),
                )
            });
            let failing = scope.spawn(move || {
                let _watch = Watch { board, armed: true };
                std::thread::sleep(Duration::from_millis(5));
                (2, Err(Stop::Failed("ss05: blocked".to_owned())))
            });
            joined(vec![waiting, queued, failing])
        });
        assert!(matches!(ended[0].1, Err(Stop::Halted)));
        assert!(matches!(ended[1].1, Err(Stop::Halted)));
        assert_eq!(
            settle_ends(ended, 3).expect_err("one configuration failed"),
            "ss05: blocked"
        );
    }
}
