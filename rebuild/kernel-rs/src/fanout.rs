//! Runs settlement configurations: one into a caller's sink, or a named set concurrently into a directory of files. The `enumerate` and `enumerate-configs` subcommands both serialize a configuration through [`run_config`], so a file the fan-out writes cannot differ from what `enumerate` writes to stdout for the same configuration.
//!
//! The output does not depend on the thread count. All configurations share one [`SpecIndex`], which has no mutable state. The engine, the window options, the two slot filters, the liveness probe, and the fiber deriver are built inside the fixpoint on each call, so each configuration has its own. Enumerations share only `default`'s finished memo, a read-only snapshot behind an [`Arc`] that each delta reads through an exclusion saying which of its windows it may use ([`crate::memo`]). `default`'s enumeration finishes before any delta starts; a configuration enumerated from scratch beside it reads no memo. `default`'s fold preparation then runs in one of the wave's worker slots and does not use the memo. A table build's folds also share what [`crate::crossconfig`] exchanges between them, each configuration's published rules and the rows it sends the others, read only at the rendezvous [`run_configs_tables`] describes, so no fold depends on when another finished.
//!
//! Parallelism stops at the configuration. A configuration's product depends only on its row set, not on the order the worklist visits windows, because a class-grain row is traced at its fiber's canonical representative. The fixpoint keeps its LIFO worklist order fixed anyway. A configuration's `cited_provenance` is what its one engine fired while tracing its windows. Splitting one configuration's worklist across threads would require the threads to share one engine's memo and fired set to reproduce the sequential result, which would cost what the split was meant to save.

use std::cmp::Reverse;
use std::collections::BTreeSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use crate::artifacts;
use crate::crossconfig::{self, ForeignRow, Imports, SharedRules, Source, SourceRows, Spellings};
use crate::engine::{EngineModes, MemoCeilings};
use crate::fixpoint::{self, EnumerationModes, MemoAccess};
use crate::fold;
use crate::hash::HashSet;
use crate::index::SpecIndex;
use crate::memo::{Exclusion, MemoSnapshot, SharedMemo, unlocking_runes};
use crate::model::Sym;
use crate::options::WindowOptions;
use crate::replay;
use crate::stream::{self, FixpointProduct};

/// One configuration to run: the token that names it (the file name, the stream head's `config`, and the label on its timing lines) and the features the token resolves to.
pub struct Configuration<'a> {
    pub token: &'a str,
    pub features: Vec<Sym>,
}

/// Which in-process memo a table build's deltas may read before they settle a window themselves ([`crate::memo`]).
///
/// - `default_memo_sharing`: `default` enumerates first, and every other configuration but those `scratch_beside_default` starts beside it then reads `default`'s finished memo for the windows that name none of its own unlocking runes. When it is off, no configuration reads `default`'s in-process memo; `tests/cli.rs` checks that both settings write the same tables.
/// - `scratch_beside_default`: in a build that shares `default`'s memo, at most how many of the deltas, taken heaviest first from [`delta_worklist`], enumerate from scratch instead, starting at the same moment as `default` rather than after it. They are taken in whole tiers of equal unlocking-rune count ([`scratch_tiers`]), and the count is capped at the delta count and at one less than the worker slots, so `default`'s enumeration and theirs never run more than the width at once. The tables are the same bytes either way.
/// - `verdict_sharing`: in a build where at least one delta reads `default`'s memo, `default` also publishes its liveness verdicts in that memo, for each distinct unlocking-rune set among those deltas ([`verdict_groups`]), and each delta is served the ones its exclusion admits, rerunning only the asks that name one of its unlocking runes ([`crate::liveness`]). A build whose deltas all enumerate from scratch publishes none, since the capture costs `default` time. The tables are the same bytes either way.
/// - `memo_ceilings`: the ceilings each configuration's engine holds its prospect and candidate memos to, [`fixpoint::TABLE_MEMO_CEILINGS`] unless the caller sets others. The tables are the same bytes at any ceiling.
#[derive(Clone, Debug)]
pub struct MemoSharing {
    pub default_memo_sharing: bool,
    pub scratch_beside_default: usize,
    pub verdict_sharing: bool,
    pub memo_ceilings: MemoCeilings,
}

impl Default for MemoSharing {
    fn default() -> Self {
        Self {
            default_memo_sharing: true,
            scratch_beside_default: 0,
            verdict_sharing: true,
            memo_ceilings: fixpoint::TABLE_MEMO_CEILINGS,
        }
    }
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
/// The count estimates how long a delta traces on its own: a delta whose features unlock more runes can use less of `default`'s memo and enumerates more itself. The estimate only has to separate the memo-heavy deltas from the cheap ones. Starting the heavy ones first ends the wave sooner when the machine runs fewer slots than there are deltas, because a heavy delta started last would run alone while the other slots sit idle. The same order picks the deltas a build starts from scratch beside `default` (`MemoSharing::scratch_beside_default`), the heaviest first, since `default`'s memo would save them the least. The order cannot change the output: each delta reads only `default`'s snapshot, never another delta's output, and each result is placed by its list position.
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

/// The unlocking-rune sets `default` publishes its liveness verdicts for: one per distinct set among the deltas that read its memo, in worklist order, and none when no delta does ([`crate::liveness`]).
fn verdict_groups(deltas: &[(usize, DeltaWork<'_>)]) -> Vec<HashSet<Sym>> {
    let mut groups: Vec<HashSet<Sym>> = Vec::new();
    for (_, work) in deltas {
        if !groups.contains(&work.unlocking) {
            groups.push(work.unlocking.clone());
        }
    }
    groups
}

/// The memo access `default`'s enumeration runs under, given the deltas that will read its memo: no shared memo, its own memo kept only when some delta reads it, its verdicts published for those deltas' unlocking-rune sets when `sharing` shares verdicts ([`verdict_groups`]), and `sharing`'s ceilings. So when every delta starts from scratch, `default` keeps no memo and releases it at its release point, as they do.
fn default_access(sharing: &MemoSharing, deltas: &[(usize, DeltaWork<'_>)]) -> MemoAccess {
    MemoAccess {
        shared_memos: Vec::new(),
        keep_memo: !deltas.is_empty(),
        publish_verdicts: if sharing.verdict_sharing {
            verdict_groups(deltas)
        } else {
            Vec::new()
        },
        memo_ceilings: sharing.memo_ceilings,
    }
}

/// How many entries at the head of the wave's worklist a build starts from scratch beside `default` when it may start up to `most`: the longest head of at most `most` entries that takes whole tiers of equal unlocking-rune count, so it ends at the end of the worklist or where the count drops. A delta whose count ties one taken would still run as a delta after `default`'s enumeration, on the build's path as long as the one taken would have been, while `default` enumerates more slowly beside the ones taken; so part of a tier costs time and saves none.
fn scratch_tiers(work: &[(usize, DeltaWork<'_>)], most: usize) -> usize {
    (0..=most.min(work.len()))
        .rev()
        .find(|&count| {
            count == 0
                || count == work.len()
                || work[count - 1].1.unlocking.len() != work[count].1.unlocking.len()
        })
        .unwrap_or(0)
}

/// One configuration's table build result: the digest [`artifacts::table_digest`] computes over its decision and join tables, and its timing lines.
#[derive(Debug)]
pub struct TableAnswer {
    pub digest: String,
    pub timed: Vec<String>,
}

/// How many exchange rounds a table build allows before it gives up. Each round that does not end the exchange imports at least one window that no configuration held before it, so the bound only catches a fault in the exchange itself.
const EXCHANGE_ROUNDS: usize = 64;

/// Builds every configuration's settlement table, join table, window file, and digest under `outdir`, running at most `workers` enumerations or folds at once. Each delta reads `default`'s memo where `sharing` allows.
///
/// Every configuration runs on a thread of its own from its enumeration to its files, because its product and window options hold `Rc`s and cannot cross threads (`EnumeratedTables`). The threads meet on a `Board`, which grants `workers` slots in ticket order and holds the rendezvous of the exchange:
///
/// - Each configuration enumerates, then prepares its fold ([`fold::Prepared`]), folds its rules from its own rows, and publishes them in canonical labels ([`crossconfig::SharedRules`]), all inside one slot.
/// - Once every configuration has published, the exchange runs in rounds ([`crossconfig`]). In each, every configuration evaluates every configuration's rules over its own rows, for the inputs any configuration refolded in the last round, and leaves each other configuration the rows that configuration answers wrongly where the shipped order lets that answer fire, and the rows one of the evaluator's own rules answers where that configuration publishes the rule's key with another outcome, wherever that configuration's own rule ships ([`crossconfig::exports`]); then every configuration takes in what it was left, refolds the inputs that gained rows, and publishes again. The rounds end when no configuration took in anything, and a configuration fails if another still publishes one of its rule keys with another outcome ([`crossconfig::one_outcome_per_key`]), which the emitter refuses. The exchange holds no slot: what a configuration holds through it is its parked product, expansion and row chains, and the evaluation adds little.
/// - Each configuration then finishes its fold inside a slot: the partition replay over its own and its imported rows, the certificates, the three artifact files, and the digest.
///
/// When `default_memo_sharing` is on and the set has the no-feature configuration and at least one other, `default` enumerates on the calling thread, taking no slot. Beside it, the head of [`delta_worklist`] that [`scratch_tiers`] takes from at most `scratch_beside_default` entries, capped at one less than `workers`, enumerates from scratch on threads of their own from the start, each taking a slot in that order; with none of them, `default` enumerates alone. `default` keeps its memo only while a delta is left to read it ([`default_access`]). The calling thread then continues as `default`'s thread, whose preparation takes the next slot, while each remaining delta takes a slot in [`delta_worklist`] order and reads `default`'s memo behind an exclusion of its own unlocking runes; `default`'s memo is freed when the last delta has enumerated. A failure or a panic in `default`'s enumeration halts the board, so the configurations enumerating beside it stop at their next turn. When `default_memo_sharing` is off, or the set has no `default` or nothing beside it, there is no in-process memo to share, and every configuration enumerates from scratch, reading no memo and keeping none, and takes its slot in list order.
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
                    let (run, sharing) = (&run, &sharing);
                    scope.spawn(move || {
                        let answer = table_thread(run, listed, config.token, listed, || {
                            enumerate_from_scratch(run, config, modes, sharing)
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
    let rest = delta_worklist(index, configs, default_position);
    let scratch = scratch_tiers(
        &rest,
        sharing.scratch_beside_default.min(workers.max(1) - 1),
    );
    let (from_scratch, deltas) = rest.split_at(scratch);
    let (run, sharing) = (&run, &sharing);
    let ended = std::thread::scope(move |scope| {
        let mut handles: Vec<_> = from_scratch
            .iter()
            .enumerate()
            .map(|(ticket, (listed, work))| {
                let listed = *listed;
                scope.spawn(move || {
                    let config = work.config;
                    let answer = table_thread(run, listed, config.token, ticket, || {
                        enumerate_from_scratch(run, config, modes, sharing)
                    });
                    (listed, answer)
                })
            })
            .collect();
        let mut watch = Watch {
            board: run.board,
            armed: true,
        };
        let pending = enumerate_config_tables(
            index,
            default,
            modes,
            report,
            default_access(sharing, deltas),
        )
        .map_err(|error| format!("{}: {error}", default.token))?;
        let memo: Option<Arc<MemoSnapshot>> = pending.memo.clone();
        handles.extend(deltas.iter().enumerate().map(|(position, (listed, work))| {
            let memo = Arc::clone(
                memo.as_ref()
                    .expect("default keeps its memo while a delta is left to read it"),
            );
            let listed = *listed;
            scope.spawn(move || {
                let config = work.config;
                let answer =
                    table_thread(run, listed, config.token, scratch + 1 + position, || {
                        let access = MemoAccess {
                            shared_memos: vec![SharedMemo {
                                memo,
                                excluded: Exclusion::of(index, work.unlocking.iter().copied()),
                            }],
                            keep_memo: false,
                            publish_verdicts: Vec::new(),
                            memo_ceilings: sharing.memo_ceilings,
                        };
                        enumerate_config_tables(index, config, modes, report, access)
                    });
                (listed, answer)
            })
        }));
        drop(memo);
        watch.armed = false;
        let lead = table_thread(run, default_position, default.token, scratch, move || {
            Ok(pending)
        });
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

/// One configuration's table build on its own thread, from its enumeration to its files, as [`run_configs_tables`] describes. `enumerate` runs inside the slot `ticket` is due. When asked, the timing lines are the enumeration's `enumerate[<config>]` after the cache stats' `[c]` lines, then `fold.prefixes[<config>]`, then, with the cache stats, `[c] <config> resident_parked`, the process's resident size once every configuration has parked its prepared product and before the exchange's first round, and on the first-listed configuration's thread `[c] <config> parked_heap`, the bytes the process's malloc zones hold allocated at that point ([`crate::fixpoint::heap_bytes`]), which every other thread waits for before the exchange starts and which `kernel_exec.PARKED_FOLD_BYTES` is set from, then `fold.exchange[<config>]` (the time this configuration spent evaluating, taking in and refolding, without the waits), and `fold.partition[<config>]` at millisecond precision, then `fold[<config>]`, which covers the preparation and the finish.
fn table_thread<'a>(
    run: &TableRun<'a>,
    listed: usize,
    token: &str,
    ticket: usize,
    enumerate: impl FnOnce() -> Result<EnumeratedTables<'a>, String>,
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
    } = enumerate().map_err(fail)?;
    drop(memo);
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
    let everyone: Vec<Arc<SharedRules>> = (0..run.board.count)
        .map(|config| run.board.rules_of(config).0)
        .collect();
    let published: Vec<&SharedRules> = everyone.iter().map(|rules| &**rules).collect();
    crossconfig::one_outcome_per_key(run.spellings, listed, &published).map_err(fail)?;
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

/// One configuration of a table build enumerated from scratch: it reads no memo and keeps none.
fn enumerate_from_scratch<'a>(
    run: &TableRun<'a>,
    config: &Configuration<'_>,
    modes: EnumerationModes,
    sharing: &MemoSharing,
) -> Result<EnumeratedTables<'a>, String> {
    let access = MemoAccess {
        memo_ceilings: sharing.memo_ceilings,
        ..MemoAccess::default()
    };
    enumerate_config_tables(run.index, config, modes, run.report, access)
}

/// One configuration enumerated and holding what its fold needs.
///
/// It cannot cross threads, because the product's label pool holds `Rc<str>` ([`crate::stream`]) and the window options hold `Rc<FollowerMap>` ([`crate::options`]). That is why each configuration of a table build runs on one thread from its enumeration to its files, and why a caller clones the memo out first: behind its [`Arc`], the memo is the only part of an enumeration that other configurations read. Only a configuration whose memo access set `keep_memo` has a memo here; every other one released its memo at the fixpoint's release point.
struct EnumeratedTables<'i> {
    product: FixpointProduct,
    options: WindowOptions<'i>,
    memo: Option<Arc<MemoSnapshot>>,
    timed: Vec<String>,
}

/// One configuration's enumeration for its tables: the fixpoint over what `access` allows, keeping the finished memo behind an [`Arc`] when `access` asks. The timing line is `enumerate[<config>]`, after the cache stats' `[c]` lines.
fn enumerate_config_tables<'i>(
    index: &'i SpecIndex,
    config: &Configuration<'_>,
    modes: EnumerationModes,
    report: Report,
    access: MemoAccess,
) -> Result<EnumeratedTables<'i>, String> {
    let token = config.token;
    let mut timed: Vec<String> = Vec::new();
    let mut stats: Vec<String> = Vec::new();
    let started = Instant::now();
    let fixpoint::TablesEnumeration {
        product,
        options,
        memo,
    } = fixpoint::enumerate_for_tables(
        index,
        &config.features,
        modes,
        report.cache_stats.then_some(&mut stats),
        access,
    )?;
    timed.append(&mut stats);
    if report.timings {
        timed.push(timing_line(
            &format!("enumerate[{token}]"),
            started.elapsed(),
        ));
    }
    Ok(EnumeratedTables {
        product,
        options,
        memo,
        timed,
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

    /// The tokens as configurations: `default`, `ss08` and `ss09` with no features, anything else with the feature its token names.
    fn configurations_of(
        index: &SpecIndex,
        tokens: &[&'static str],
    ) -> Vec<Configuration<'static>> {
        tokens
            .iter()
            .map(|token| Configuration {
                token,
                features: if matches!(*token, "default" | "ss08" | "ss09") {
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

    /// Memo sharing with as many deltas as the width leaves room for enumerated from scratch beside `default`: none at one worker, the heavy delta at two, and both at eight.
    fn beside_default() -> MemoSharing {
        MemoSharing {
            scratch_beside_default: usize::MAX,
            ..MemoSharing::default()
        }
    }

    /// The windows one configuration took from a shared memo, as the `shared_memo_hits` line among its cache stats counts them. The line names the configuration by its features, so a delta without any, such as `ss09`, reports under `default`.
    fn shared_memo_hits(timed: &[String]) -> u64 {
        timed
            .iter()
            .find_map(|line| {
                line.strip_prefix("[c] ")?
                    .split_once(" shared_memo_hits count=")
                    .map(|(_, count)| count)
            })
            .unwrap_or_else(|| panic!("the cache stats report the shared memo hits: {timed:?}"))
            .parse()
            .expect("as a count")
    }

    /// What one width of the table fan-out wrote: every configuration's digest, and every configuration's files as [`table_files`] reads them.
    type Filed = (Vec<String>, Vec<Vec<(String, Vec<u8>)>>);

    /// The files one configuration's table build writes, read back as bytes: the three tables.
    fn table_files(outdir: &Path, token: &str) -> Vec<(String, Vec<u8>)> {
        ["settlement", "joins", "windows"]
            .iter()
            .map(|family| format!("{family}-{token}.tsv"))
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

    /// The table fan-out writes the same bytes at every width and on every schedule, over the set whose claim order differs from its listed order. Tables and digests match per configuration at 0, 1, 2, and 8 workers. At 2, one worker takes the heavy delta and the lead claims the cheap one after `default`'s fold, which is the case the claim order is for. At 8, the deltas enumerate while `default`'s fold runs on the calling thread. They match too when every configuration enumerates from scratch, which shows that reading `default`'s memo changes no table, and when `default` publishes no verdicts, so that every delta runs every liveness probe instead of being served `default`'s. So must those of the case that enumerates deltas from scratch beside `default`, as many as the width leaves room for: none at 0 and 1 workers, the heavy delta at 2, and both at 8, where no delta reads `default`'s memo and `default` keeps none. Every run reports its cache stats, whose `shared_memo_hits` lines show that each case ran its own schedule: a delta over `default`'s memo takes windows from it, and a configuration enumerated from scratch takes none.
    #[test]
    fn a_table_fan_out_files_the_same_bytes_at_every_width() {
        let index = fixtures::mini();
        let configs = permuting(&index);
        let root = scratch("fan-out-tables");
        let mut expected: Option<Filed> = None;
        for (arm, sharing) in [
            ("shared", MemoSharing::default()),
            (
                "from scratch",
                MemoSharing {
                    default_memo_sharing: false,
                    ..MemoSharing::default()
                },
            ),
            (
                "without verdict sharing",
                MemoSharing {
                    verdict_sharing: false,
                    ..MemoSharing::default()
                },
            ),
            ("beside default", beside_default()),
        ] {
            for workers in [0, 1, 2, 8] {
                let outdir = root.join(arm).join(format!("at-{workers}"));
                let answers = run_configs_tables(
                    &index,
                    &configs,
                    DEFAULT_MODES,
                    &outdir,
                    INPUTS,
                    workers,
                    Report {
                        timings: false,
                        cache_stats: true,
                    },
                    sharing.clone(),
                )
                .expect("every configuration answers");
                assert_eq!(answers.len(), configs.len());
                let run = format!("{arm} at {workers} workers");
                let from_scratch: &[&str] = match (arm, workers) {
                    ("from scratch", _) | ("beside default", 8) => &["ss09", "ss03"],
                    ("beside default", 2) => &["ss03"],
                    _ => &[],
                };
                for (config, answer) in configs.iter().zip(&answers) {
                    let token = config.token;
                    let hits = shared_memo_hits(&answer.timed);
                    if token == "default" || from_scratch.contains(&token) {
                        assert_eq!(hits, 0, "{run}: {token} reads no shared memo");
                    } else {
                        assert!(hits > 0, "{run}: {token} reads default's memo");
                    }
                }
                let digests: Vec<String> =
                    answers.iter().map(|answer| answer.digest.clone()).collect();
                let files: Vec<_> = configs
                    .iter()
                    .map(|config| table_files(&outdir, config.token))
                    .collect();
                if let Some((expected_digests, expected_files)) = &expected {
                    assert_eq!(&digests, expected_digests, "{run}");
                    same_files(
                        &files,
                        expected_files,
                        &run,
                        "the shared case at no workers",
                    );
                } else {
                    expected = Some((digests, files));
                }
            }
        }
        std::fs::remove_dir_all(&root).expect("the scratch directory is removable");
    }

    /// A table build's timing lines come back in the caller's configuration order, with each configuration's phases in the order they ran, at any width, and with the deltas enumerated from scratch beside `default`. `default`'s fold lines follow its enumerate line even when its preparation ran alongside the deltas, and `ss03`'s lines come last although its slot comes first among the deltas.
    #[test]
    fn a_table_build_times_its_phases_in_configuration_order_at_any_width() {
        let index = fixtures::mini();
        let configs = permuting(&index);
        let root = scratch("fan-out-tables-timings");
        for (arm, sharing, workers) in [1, 2, 8].into_iter().flat_map(|workers| {
            [
                ("shared", MemoSharing::default(), workers),
                ("beside default", beside_default(), workers),
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
                    "fold.prefixes[default]",
                    "fold.exchange[default]",
                    "fold.partition[default]",
                    "fold[default]",
                    "enumerate[ss09]",
                    "fold.prefixes[ss09]",
                    "fold.exchange[ss09]",
                    "fold.partition[ss09]",
                    "fold[ss09]",
                    "enumerate[ss03]",
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

    /// A spec whose `default` enumeration fails and whose `ss03` one closes: the label collision `fixpoint::tests::one_window_label_reached_from_two_left_states_stops_the_build` builds, where `qsPea.half` exits at two heights at one y, with a refusal of its `floor` exit gated on `ss03`, so under `ss03` it keeps one exit at that y and no two left states share a label.
    fn labels_collide_but_under_ss03() -> SpecIndex {
        let rows = |heights: &[&str]| {
            let built: Vec<(&str, String)> = heights
                .iter()
                .map(|height| (*height, fixtures::row(height, &[])))
                .collect();
            let entries: Vec<(&str, &str)> = built
                .iter()
                .map(|(height, row)| (*height, row.as_str()))
                .collect();
            fixtures::map(&entries)
        };
        let rune = |name: &str, stance: &str, entries: &[&str], exits: &[&str], policy: &str| {
            let surface =
                fixtures::surface(&[("entries", &rows(entries)), ("exits", &rows(exits))]);
            let stances =
                fixtures::map(&[(stance, &fixtures::stance(stance, &[("surface", &surface)]))]);
            fixtures::rune(name, &[("stances", &stances), ("policy", policy)])
        };
        let chain = ["qsTea", "qsMay", "qsPea", "qsTea"]
            .iter()
            .rev()
            .fold(None, |then: Option<String>, family| {
                let family = fixtures::names(&[family]);
                Some(match then {
                    None => fixtures::condition(&[("family", &family)]),
                    Some(then) => fixtures::condition(&[("family", &family), ("then", &then)]),
                })
            })
            .expect("the chain names four slots");
        let prefer = fixtures::record(&[
            ("kind", "\"prefer\""),
            ("cell", &fixtures::map(&[("exit", "\"floor\"")])),
            ("when", &fixtures::when(&[("right", &chain)])),
        ]);
        let refuse = fixtures::record(&[
            ("kind", "\"refuse\""),
            ("exit", "\"floor\""),
            ("when", &fixtures::when(&[("feature", "\"ss03\"")])),
        ]);
        let pea_policy = fixtures::policy(&[
            ("prefer", &fixtures::seq(&[&prefer])),
            ("refuse", &fixtures::seq(&[&refuse])),
        ]);
        let plain = fixtures::policy(&[]);
        let runes = fixtures::map(&[
            (
                "qsPea",
                &rune("qsPea", "half", &[], &["baseline", "floor"], &pea_policy),
            ),
            (
                "qsTea",
                &rune(
                    "qsTea",
                    "plain",
                    &["baseline", "floor"],
                    &["baseline"],
                    &plain,
                ),
            ),
            ("qsMay", &rune("qsMay", "plain", &["baseline"], &[], &plain)),
        ]);
        let registry = fixtures::registry(&[
            (
                "heights",
                &fixtures::map(&[("baseline", "0"), ("floor", "0"), ("x-height", "5")]),
            ),
            (
                "boundary_tokens",
                &fixtures::map(&[
                    ("space", r#"{"codepoint":32,"splits_runs":true}"#),
                    ("zwnj", r#"{"codepoint":8204,"splits_runs":false}"#),
                ]),
            ),
            (
                "families",
                &fixtures::map(&[
                    ("qsPea", r#"{"codepoint":58960,"sequence":null}"#),
                    ("qsTea", r#"{"codepoint":58962,"sequence":null}"#),
                    ("qsMay", r#"{"codepoint":58981,"sequence":null}"#),
                ]),
            ),
        ]);
        fixtures::index_of(&fixtures::dump(&runes, &registry))
    }

    /// A `default` whose enumeration fails while configurations enumerate from scratch beside it halts the board, so they stop instead of waiting at the rendezvous for it, and the run reports `default`'s error: over [`labels_collide_but_under_ss03`], where `ss03` alone closes, at the width that starts it beside `default`.
    #[test]
    fn a_default_that_fails_beside_configurations_from_scratch_is_what_the_run_reports() {
        let index = labels_collide_but_under_ss03();
        let root = scratch("fan-out-tables-default-fails-beside");
        run_configs_tables(
            &index,
            &configurations_of(&index, &["ss03"]),
            DEFAULT_MODES,
            &root.join("ss03-alone"),
            INPUTS,
            8,
            Report::timed(false),
            MemoSharing::default(),
        )
        .expect("ss03 closes on its own");
        let error = run_configs_tables(
            &index,
            &configurations(&index),
            DEFAULT_MODES,
            &root.join("beside"),
            INPUTS,
            8,
            Report::timed(false),
            beside_default(),
        )
        .expect_err("default's labels collide");
        assert!(
            error.starts_with(&format!("{}: window ", TOKENS[0])),
            "the error names the configuration that failed: {error}"
        );
        assert!(
            error.contains("reached from two left states sharing one label"),
            "and the collision it failed on: {error}"
        );
        std::fs::remove_dir_all(&root).expect("the scratch directory is removable");
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

    /// `default` keeps its memo and publishes its verdicts, once for each distinct unlocking-rune set among the deltas that read its memo, in worklist order, only while some delta reads it. When none does, as when every delta enumerates from scratch beside it, it keeps no memo and publishes nothing. `ss08` and `ss09` unlock nothing, so beside `ss03` the three deltas make two sets.
    #[test]
    fn default_keeps_its_memo_and_publishes_its_verdicts_only_for_the_deltas_over_it() {
        let index = fixtures::mini();
        let configs = configurations_of(&index, &["default", "ss09", "ss03", "ss08"]);
        let work = delta_worklist(&index, &configs, 0);
        let may = fixtures::sym(&index, "qsMay");
        assert_eq!(
            verdict_groups(&work),
            [HashSet::from_iter([may]), HashSet::default()]
        );
        assert!(verdict_groups(&work[work.len()..]).is_empty());
        let sharing = MemoSharing::default();
        let read = default_access(&sharing, &work);
        assert!(read.keep_memo);
        assert_eq!(read.publish_verdicts, verdict_groups(&work));
        let unread = default_access(&sharing, &work[work.len()..]);
        assert!(!unread.keep_memo, "a memo no delta reads is released");
        assert!(unread.publish_verdicts.is_empty());
    }

    /// A build starts deltas from scratch beside `default` in whole tiers of equal unlocking-rune count. Over `default`, `ss03`, which unlocks `qsMay`, and two deltas that unlock nothing, the worklist is `ss03` then the two tied ones: up to one entry takes `ss03`, up to two still takes only `ss03` because the second would split the tie, and three or more take every delta.
    #[test]
    fn the_deltas_started_from_scratch_are_whole_tiers_of_the_worklist() {
        let index = fixtures::mini();
        let configs = configurations_of(&index, &["default", "ss03", "ss09", "ss08"]);
        let work = delta_worklist(&index, &configs, 0);
        let taken: Vec<usize> = (0..=5).map(|most| scratch_tiers(&work, most)).collect();
        assert_eq!(taken, [0, 1, 1, 3, 3, 3]);
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
