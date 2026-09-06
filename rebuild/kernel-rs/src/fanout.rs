//! Running configurations: one of them into whatever sink a verb hands over, and a whole named set of them concurrently into a directory of streams (sub-issue #46). This is where `enumerate` and `enumerate-configs` become the same answer written twice — both turn a configuration into bytes here and nowhere else, so a file the fan-out wrote and the stdout one enumeration writes cannot drift apart.
//!
//! Byte-identity across thread counts is a property of the arrangement rather than of a comparison. One [`SpecIndex`] is shared, and it can only be shared: nothing on it is mutable and nothing in it has interior mutability, so every configuration reads the same spec and none can disturb it. Everything else — the engine, the window options, the two slot filters, the liveness probe, the fiber deriver — is built inside [`crate::fixpoint::enumerate_transitions`], per call, which is to say per configuration. The one thing that crosses between configurations is a finished memo, read-only behind an [`Arc`] and behind the exclusion that says which of its windows the reader may take ([`crate::memo`]), and it crosses only from `default`, which has finished before any reader starts; so no schedule can be observed in the output.
//!
//! Parallelism stops at the configuration, and declining to go finer is a decision rather than an omission: the product is a function of the row set — a class-grain row is traced at its fiber's canonical representative, so no traversal order reaches the bytes — but the worklist's LIFO drain order is held as contract anyway, and `cited_provenance` is what the one engine fired while tracing that configuration's windows, so a worklist split across threads would have to reproduce one engine's memo and journal across several to give the sequential answer, which is the whole cost the split would have been trying to avoid.

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
use crate::index::SpecIndex;
use crate::memo::{
    Exclusion, MemoBase, MemoHead, MemoSnapshot, memo_path, read_memo, unlocking_runes, write_memo,
};
use crate::model::Sym;
use crate::replay;
use crate::stream;

/// One configuration a run answers: the token it is spelled by — the filename, the stream head's `config`, and the label of its timing lines — and the features that token resolved to.
#[derive(Clone)]
pub struct Configuration<'a> {
    pub token: &'a str,
    pub features: Vec<Sym>,
}

/// What a table build may read before it settles a window itself, and what it leaves for the next one ([`crate::memo`]). `config_seed` is the per-configuration delta: `default` enumerates first, alone, and every other configuration then reads its finished memo for the windows naming none of its own unlocking runes; off, every configuration enumerates from scratch, which is the from-scratch arm the delta is held byte-identical to. `seed_dir` is a previous build's memo files, read as a base per configuration behind an exclusion naming `edited`, the runes whose content moved since that build, and `moved_classes`, the predicate classes whose membership did; a configuration with no file there reads nothing. `memo_stamp` is the stamp under which this build writes its own memo files beside its tables, and a build handed none writes none.
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

/// The memo file one configuration's build writes when it is done: where, under which head, and which bases' admitted windows ride along with its own so the file is the union a later build reads.
pub struct MemoFile {
    pub path: PathBuf,
    pub head: MemoHead,
    pub carried: Vec<MemoBase>,
}

/// A previous build's memo for one configuration, read from the seed directory through `keep`, or `None` where the directory holds no file for it. A file that is there but is not this configuration's, or not this world's, is a refusal rather than a silent miss, because a seed handed over by name was meant to be read.
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

/// The memo file one configuration writes under this seeding, or `None` when the build was handed no stamp to write under.
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

/// A base over a previous memo behind an exclusion, or nothing where there is no previous memo.
fn base_over(memo: Option<&Arc<MemoSnapshot>>, excluded: Exclusion) -> Option<MemoBase> {
    memo.map(|memo| MemoBase {
        memo: Arc::clone(memo),
        excluded,
    })
}

/// Why one configuration did not answer, told apart rather than worded here, because who a failure blames is the caller's knowledge: the verb writing to stdout blames the spec for a refusal and the stream itself for a write that failed, and the verb writing files names the configuration for either.
#[derive(Debug)]
pub enum Failure {
    /// The fixpoint or the emitter would not answer this configuration, in that module's own sentence.
    Refused(String),
    /// The sink the stream was being written to would not take it.
    Sink(std::io::Error),
}

/// What a configuration's stream is filed under. Spelled once because a run both writes these names and sweeps for them, and two spellings that drifted would either delete this run's own answer or leave the last run's behind.
const STREAM_PREFIX: &str = "transitions-";
const STREAM_SUFFIX: &str = ".ndjson";

/// The file one configuration's stream is written to under an output directory. The token is the whole name past the prefix, which is why a non-canonical one is refused before a run ever starts.
pub fn transitions_path(outdir: &Path, token: &str) -> PathBuf {
    outdir.join(format!("{STREAM_PREFIX}{token}{STREAM_SUFFIX}"))
}

/// The ceiling on a caller-named `--threads` — the machine's own parallelism, or one where it has none to give. It is QoS-blind: verified answering the full logical core count while confined to efficiency cores under `taskpolicy -b`, which is exactly the situation `make test-slowly` creates.
pub fn available_threads() -> usize {
    std::thread::available_parallelism().map_or(1, std::num::NonZero::get)
}

/// One phase's wall clock in `run_m1.py`'s spelling, `[t] <label> <secs>s` at one decimal, which is the shape `cycle_timings.py` recovers a child's phases from.
pub fn timing_line(label: &str, elapsed: Duration) -> String {
    format!("[t] {label} {:.1}s", elapsed.as_secs_f64())
}

/// What a run says about itself on stderr beyond its answer: the two phase timings, and the cache census the RAM work reads. Both are off by default, and a run with neither says nothing at all on a clean exit — which the identity harness relies on.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub timings: bool,
    pub census: bool,
}

impl Report {
    /// The report a run makes when it is only being timed.
    pub fn timed(timings: bool) -> Self {
        Self {
            timings,
            census: false,
        }
    }

    /// Whether this run says anything at all.
    pub fn silent(self) -> bool {
        !self.timings && !self.census
    }
}

/// One configuration answered into `sink`: its fixpoint, its stream, and the stream written — with the two phases named as `enumerate[<config>]` and `emit[<config>]` when the caller wants them timed, and the census's `[c]` lines ahead of them when it wants those. Nothing is prefixed onto either complaint here; see [`Failure`] for why the wording is left to whoever called.
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

/// Every configuration's stream written under `outdir`, at most `workers` of them in flight, with each one's timing lines returned in the order the caller named its configurations.
///
/// The directory is made with its parents, as the Python artifact writers make theirs, and a file already sitting where a configuration's stream goes is overwritten rather than refused. Every other `transitions-*.ndjson` there is swept first, because a consumer globbing the directory after a clean exit would otherwise read a configuration this run never answered as one of its answers. [`claim_all`] carries the scheduling and the failure rule.
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

/// Every configuration answered by `answer`, at most `workers` of them in flight, with each answer returned at the seat its configuration was named in.
///
/// The worklist is a seat counter and nothing else: a worker claims the next configuration, answers it, and claims again, so one worker walks the whole list in listed order and several share it out without a plan. Order is recovered from the seat each answer carries rather than from the order the answers arrived in, which is what makes a caller's stderr and stdout a function of the plan alone.
///
/// A `workers` of 0 is a run at one worker rather than a run that claims nothing: the count caps concurrency, and no cap can mean fewer than the one worker it takes to walk the list. The first failure stops further claims, since a run whose exit is nonzero says nothing about the directory it half filled, and the complaint reported is the earliest-seated of those any worker reached.
fn claim_all<T: Send>(
    configs: &[Configuration<'_>],
    workers: usize,
    answer: impl Fn(&Configuration<'_>) -> Result<T, String> + Sync,
) -> Result<Vec<T>, String> {
    let workers = workers.max(1);
    let next = AtomicUsize::new(0);
    let stop = AtomicBool::new(false);
    let answer = &answer;
    let claimed = std::thread::scope(|scope| {
        let handles: Vec<_> = (0..workers)
            .map(|_| {
                scope.spawn(|| {
                    let mut mine: Vec<(usize, T)> = Vec::new();
                    while !stop.load(Ordering::Relaxed) {
                        let seat = next.fetch_add(1, Ordering::Relaxed);
                        let Some(config) = configs.get(seat) else {
                            break;
                        };
                        match answer(config) {
                            Ok(answered) => mine.push((seat, answered)),
                            Err(complaint) => {
                                stop.store(true, Ordering::Relaxed);
                                return Err((seat, complaint));
                            }
                        }
                    }
                    Ok(mine)
                })
            })
            .collect();
        handles
            .into_iter()
            .map(|handle| {
                handle
                    .join()
                    .unwrap_or_else(|panic| std::panic::resume_unwind(panic))
            })
            .collect::<Vec<_>>()
    });
    let mut seated: Vec<Option<T>> = (0..configs.len()).map(|_| None).collect();
    let mut failure: Option<(usize, String)> = None;
    for outcome in claimed {
        match outcome {
            Ok(answered) => {
                for (seat, one) in answered {
                    seated[seat] = Some(one);
                }
            }
            Err((seat, complaint)) => {
                if failure.as_ref().is_none_or(|(worst, _)| seat < *worst) {
                    failure = Some((seat, complaint));
                }
            }
        }
    }
    match failure {
        Some((_, complaint)) => Err(complaint),
        None => Ok(seated
            .into_iter()
            .map(|one| one.expect("a run with no failure seated every configuration"))
            .collect()),
    }
}

/// What one configuration's table build answered: the contract digest of its two tables, and its timing lines.
pub struct TableAnswer {
    pub digest: String,
    pub timed: Vec<String>,
}

/// Every configuration's two tables, its window enumeration and its digest, written under `outdir` at most `workers` at a time, each configuration reading what the seeding lets it read and leaving the memo file it says to leave.
///
/// Under the configuration seed, the no-feature configuration runs first and alone, keeping its memo; the rest then run at the given width, each reading that memo behind an exclusion naming its own unlocking runes, so the wall is one full enumeration plus one wave of deltas and the memo is held once, shared. A previous build's memo, where the seeding names one, is read behind the edited runes: `default` reads its own previous file whole, and a delta configuration reads `default`'s previous file behind the edited runes and its own unlocking runes together, and its own previous file only for the windows naming one of its unlocking runes, which is the one part of it the in-process memo cannot answer. A set without the no-feature configuration, or one configuration alone, has no in-process memo to seed from and every configuration reads its own previous file whole. Answers come back seated in the order the configurations were named, whatever order they ran in.
///
/// Nothing is swept first, unlike the stream fan-out: `run_m1.build_tables` writes into the build's own artifact directory beside a dozen other families, and a run that deleted the tables of a configuration set the build no longer names would be answering a question nobody asked it.
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
    let edited = Exclusion::of(seeding.edited.iter().copied())
        .with_classes(seeding.moved_classes.iter().copied());
    let default_seat = seeding
        .config_seed
        .then(|| configs.iter().position(|config| config.features.is_empty()))
        .flatten()
        .filter(|_| configs.len() > 1);
    let Some(default_seat) = default_seat else {
        return claim_all(configs, workers, |config| {
            let previous = load_seed(index, &seeding, config.token, &world, |_| true)?;
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
                .map(|(answer, _)| answer)
                .map_err(|complaint| format!("{}: {complaint}", config.token))
        });
    };
    let default = &configs[default_seat];
    let previous_default = load_seed(index, &seeding, default.token, &world, |_| true)
        .map_err(|complaint| format!("{}: {complaint}", default.token))?;
    let (default_answer, memo) = run_config_tables(
        index,
        default,
        modes,
        outdir,
        inputs,
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
    let memo: Arc<MemoSnapshot> =
        Arc::new(memo.expect("a kept memo comes back from a trace-memo enumeration"));
    let rest: Vec<Configuration<'_>> = configs
        .iter()
        .enumerate()
        .filter(|(seat, _)| *seat != default_seat)
        .map(|(_, config)| config.clone())
        .collect();
    let mut answers = claim_all(&rest, workers, |config| {
        let unlocking = unlocking_runes(index, &config.features);
        let previous_own = load_seed(index, &seeding, config.token, &world, |key| {
            key.runes_named().any(|rune| unlocking.contains(&rune))
        })?;
        let mut bases = vec![MemoBase {
            memo: Arc::clone(&memo),
            excluded: Exclusion::of(unlocking.iter().copied()),
        }];
        bases.extend(base_over(
            previous_default.as_ref(),
            Exclusion::of(unlocking.iter().chain(edited.runes()).copied())
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
            .map(|(answer, _)| answer)
            .map_err(|complaint| format!("{}: {complaint}", config.token))
    })?;
    answers.insert(default_seat, default_answer);
    Ok(answers)
}

/// One configuration folded in place: its fixpoint over whatever the seed lets it read, the fold over the product that fixpoint still holds — the rule certificates closed over the enumeration's own [`crate::options::WindowOptions`], so the guard is swept once — the three artifact files, and the digest of the pair, with the finished memo beside the answer when the seed asked to keep it and written to `file` when one is named. The phases are named `enumerate[<config>]`, `memo[<config>]` and `fold[<config>]` when the caller wants them timed, the census's `[c]` lines riding ahead of them as they do for a stream run.
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
) -> Result<(TableAnswer, Option<MemoSnapshot>), String> {
    let token = config.token;
    let mut timed: Vec<String> = Vec::new();
    let mut census: Vec<String> = Vec::new();
    let started = Instant::now();
    let keep_memo = seed.keep_memo;
    let seed = Seed {
        bases: seed.bases,
        keep_memo: keep_memo || file.is_some(),
    };
    let (product, mut options, memo) = fixpoint::enumerate_for_tables(
        index,
        &config.features,
        modes,
        report.census.then_some(&mut census),
        seed,
    )?;
    timed.append(&mut census);
    if report.timings {
        timed.push(timing_line(
            &format!("enumerate[{token}]"),
            started.elapsed(),
        ));
    }
    let memo = match file {
        Some(file) => {
            let started = Instant::now();
            let own = memo
                .as_ref()
                .expect("a memo file is written from a kept memo");
            write_memo(index, &file.path, &file.head, own, &file.carried)?;
            if report.timings {
                timed.push(timing_line(&format!("memo[{token}]"), started.elapsed()));
            }
            memo.filter(|_| keep_memo)
        }
        None => memo,
    };
    let started = Instant::now();
    let folded = fold::fold_with(index, product, &mut options)?;
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
    Ok((TableAnswer { digest, timed }, memo))
}

/// What one configuration's string replay answered: the walk's counts, and its timing line when one was asked for.
pub struct ReplayAnswer {
    pub report: replay::Report,
    pub timed: Vec<String>,
}

/// Every configuration's persisted rules replayed over `universe`, at most `workers` at a time: each one reads `<outdir>/settlement-<config>.tsv` back, walks the universe's texts, and holds the rules' first-match answer to the engine's own settlement window by window. The world is the enumeration's, minus the grain: a replay settles single windows, which have no grain to name.
pub fn run_configs_replay(
    index: &SpecIndex,
    configs: &[Configuration<'_>],
    modes: EnumerationModes,
    outdir: &Path,
    universe: replay::Universe<'_>,
    workers: usize,
    report: Report,
) -> Result<Vec<ReplayAnswer>, String> {
    claim_all(configs, workers, |config| {
        run_config_replay(index, config, modes, outdir, universe, report)
            .map_err(|complaint| format!("{}: {complaint}", config.token))
    })
}

/// One configuration replayed: its rules read back, the walk run, and the phase named `replay[<config>]` when the caller wants it timed.
pub fn run_config_replay(
    index: &SpecIndex,
    config: &Configuration<'_>,
    modes: EnumerationModes,
    outdir: &Path,
    universe: replay::Universe<'_>,
    report: Report,
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
    let walked = walk.walk_universe(universe)?;
    let mut timed: Vec<String> = Vec::new();
    if report.timings {
        timed.push(timing_line(&format!("replay[{token}]"), started.elapsed()));
    }
    Ok(ReplayAnswer {
        report: walked,
        timed,
    })
}

fn write_text(path: &Path, text: &str) -> Result<(), String> {
    std::fs::write(path, text).map_err(|error| format!("{}: {error}", path.display()))
}

/// One configuration answered into its own file under `outdir`, which is the only thing a worker does. Every complaint names the configuration, since a caller running several has no other way to tell which one failed, and one the filesystem raised names the file it raised it about.
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

/// Every `transitions-*.ndjson` already under `outdir` that this run does not name, removed before any configuration writes.
///
/// The sweep is exactly the pattern [`transitions_path`] spells and nothing wider. An output directory holds whatever its owner put there, and a run that swept anything else would be answering a question nobody asked it.
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

    /// The world every test below runs in — the shipping one, where the deep slots enumerate at class grain and the representative a fiber's first visitor fixes is output-visible, which is the world byte-identity across schedules is worth asserting in.
    const SHIPPING: EnumerationModes = EnumerationModes {
        simulated_prospect: true,
        vote_slots: true,
        deep_classes: true,
    };

    /// The two configurations the fixture can tell apart: it unlocks a `qsMay` entry under `ss03` and nothing under nothing.
    const TOKENS: [&str; 2] = ["default", "ss03"];

    /// A scratch path of this test's own, cleared first so a stale file cannot stand in for one a run was supposed to write, and left uncreated so that making it is the run's own job. It lives under `target/`, which is gitignored, rather than in the system temp directory.
    fn scratch(name: &str) -> PathBuf {
        let directory = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target/test-scratch")
            .join(name);
        let _ = std::fs::remove_dir_all(&directory);
        directory
    }

    fn configurations(index: &SpecIndex) -> Vec<Configuration<'static>> {
        TOKENS
            .iter()
            .map(|token| Configuration {
                token,
                features: if *token == "default" {
                    Vec::new()
                } else {
                    vec![fixtures::sym(index, token)]
                },
            })
            .collect()
    }

    /// The bytes `enumerate` writes for one configuration, which is [`run_config`] into a buffer — the same call the verb makes, differing only in where it points.
    fn enumerated(index: &SpecIndex, config: &Configuration<'_>) -> String {
        let mut sink: Vec<u8> = Vec::new();
        run_config(index, config, SHIPPING, &mut sink, Report::timed(false))
            .expect("the fixture's fixpoint closes and serializes");
        String::from_utf8(sink).expect("a transitions stream is text")
    }

    /// A directory sitting exactly where a configuration's stream goes, which is how a worker is made to fail inside a run rather than before one.
    fn block(outdir: &Path, token: &str) {
        std::fs::create_dir_all(transitions_path(outdir, token))
            .expect("a directory can occupy a stream's path");
    }

    /// The whole fan-out against the bytes one enumeration at a time writes, at one thread and at more threads than there are configurations, into a directory neither run was given.
    ///
    /// This is the exit bar's own claim at fixture scale: the files a concurrent run leaves behind are the files a serial run would, letter for letter, because the only thing the configurations share is a spec nothing can write to.
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

    /// The timing lines a run buffers arrive in the caller's own configuration order whatever the thread count, and name every configuration's two phases.
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

    /// A run without `--timings` says nothing at all, which is what lets the identity harness read any stderr on a clean exit as a failure.
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

    /// The `[t]` line's shape, which `cycle_timings.py`'s `_INNER_LINE` has to match and `run_m1.py`'s own lines already do: the marker, the label, the seconds at one decimal, and the trailing `s`.
    #[test]
    fn a_timing_line_is_the_one_decimal_shape_the_cycle_parses() {
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
    }

    /// The name a configuration's stream is filed under, which is the caller's own token and nothing added to it — a caller that named the configurations knows every filename before the run starts.
    #[test]
    fn a_configuration_files_its_stream_under_its_own_token() {
        assert_eq!(
            transitions_path(Path::new("out"), "ss03+ss05"),
            Path::new("out/transitions-ss03+ss05.ndjson")
        );
    }

    /// A stream that cannot even be created stops the run, and the complaint carries both the configuration and the path the filesystem refused.
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

    /// With every seat blocked and a worker for each, the complaint a run reports is the earliest-seated one — whichever worker reached it, and however many of the others got far enough to fail too. Seat 0 is always claimed, since a worker only stops claiming once someone else has failed, so the run's word is the first configuration's every time.
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

    /// A run sweeps the streams of configurations it was not asked about, so that a directory globbed after a clean exit is this run's answer and nothing else, and leaves everything that is not a stream where it found it.
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

    /// A thread count of 0 is a run at one worker: the count caps concurrency, and a cap of none cannot mean a run that answers nothing.
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
}
