//! `ams-m1-kernel` — the Rust reimplementation of the M1 settlement kernel (tracker issue #40). Today it does the ingest step, the settlement core and the table build's kernel half: it reads an `ams-m1-spec/1` dump into the interned model, echoes that model back out in canonical form (sub-issue #42), settles single windows against it — one case file at a time for the differential, and the whole late-formation surface for the guard (sub-issue #43) — runs the whole table-build worklist fixpoint over one configuration in either candidacy world and at either deep-slot grain, writing the transitions stream Python folds into its artifacts (sub-issues #44 and #45), and answers deep-slot liveness and fiber questions one key at a time for the liveness-grain differential (sub-issue #45).
//!
//! **`rebuild/pipeline/kernel_io.py` is the binding contract for the dump, and `rebuild/pipeline/settle.py` with `rebuild/pipeline/specificity.py` for the settlement.** Their module and function docstrings define both halves of each boundary, and this crate is measured against them rather than the other way around: the dump is whatever `kernel_io.spec_json` writes, the strictness is whatever `kernel_io.spec_of` enforces, a settled window is whatever `settle.Engine.transition_trace` returns down to its raise messages, and where this crate and those modules disagree, those modules are right. `rebuild/pipeline/table.py` is the contract for the fixpoint and for everything the deep slots do. `bench-the-rebuild/RUST-PORT-PLAN.md` carries the design facts behind the port — chiefly that the packing, not the language, is the win, and that the standard SipHash hasher beat the finalizer-less fast hasher that a first pass reached for.
//!
//! **A change to `rebuild/pipeline/model.py` is a cross-group coordination event, and it lands on this crate too.** The Python codec is driven by `dataclasses.fields`, so a new field rides the dump with no edit there; this crate spells its field sets by hand and will therefore refuse the new dump rather than silently drop the field. `make kernel-parity` is what catches the lag, and it catches it as a byte diff on the very next run.
//!
//! Six make targets drive the crate from the repo root: `make kernel-build` compiles the release binary the harnesses run, `make kernel-check` is the crate's own gate (`cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`), `make kernel-parity` echoes the live alphabet and every rung of the nested ladder through the binary and compares the bytes against Python, `make kernel-differential` settles every window of the golden corpus, a seeded fuzz sweep, and the exhaustive formation-guard surface on both sides and compares those bytes too, `make kernel-fixpoint` enumerates whole configurations on both sides and compares the stream byte for byte together with the three artifacts and the digest Python folds out of it, and `make kernel-liveness` sweeps the deep-slot filters and the fiber partitions key by key against Python's own.
//!
//! The CLI is positional arguments and a hand-rolled flag scan, never an argument parser, and stdout carries the answer and nothing else, ever. Three flags name the world a verb answers in, and all three are spelled as negations of the shipping configuration — `--candidacy-prospect`, `--vote-slots-off`, `--deep-classes-off` — so a bare invocation is what ships and every departure from it is visible in the command line:
//!
//! - `ams-m1-kernel spec-echo <spec>` writes the canonical dump plus one newline.
//! - `ams-m1-kernel settle-cases <spec> <cases> [--features=a,b,…] [--candidacy-prospect] [--vote-slots-off]` replays a plain-text `ams-m1-corpus/3` case file — the harness gunzips it, because this crate carries serde_json and nothing else — through one engine in file order and writes one re-emitted case line per case. A window that raises a settlement error is a normal result line and never a nonzero exit.
//! - `ams-m1-kernel guard-sweep <spec>` writes the whole section 5.7 late-formation surface, one tab-separated verdict per line.
//! - `ams-m1-kernel enumerate <spec> [--features=a,b,…] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off]` runs one configuration's whole table-build fixpoint and writes the uncompressed `ams-m1-transitions/1` stream — the head line and one row per window. `--deep-classes-off` is Python's `AMS_DEEP_CLASSES=0`, the label-grain arm; in the pinned candidacy world enumeration is label-grain regardless, so the flag is accepted and does nothing there. The harness gzips the stream, as it gunzips the case files, for the same reason.
//! - `ams-m1-kernel liveness-cases <spec> <keys> [--features=a,b,…] [--candidacy-prospect] [--vote-slots-off]` answers one deep-slot question per key line: `3<tab><input><tab><r1><tab><r2>` and `4<tab><input><tab><r1><tab><r2><tab><r3>` answer `live` or `dead` — the full filter verdict, chain arm and liveness arm together — and `fibers<tab><input><tab><r1><tab><r2>` answers with the context's fiber partition as compact JSON. Every name is a rune family name; a key naming anything else stops the run. Each output line is the key line, a tab, and the answer, in file order.
//!
//! A usage mistake — wrong argument count, wrong verb, an unknown flag, an argument that is not valid Unicode — exits 2; a file that cannot be read, parsed, or validated, a case file or key file this build cannot answer, and a window that will not settle, exit 1 with a one-line complaint on stderr.

#![forbid(unsafe_code)]

use std::io::Write;
use std::process::ExitCode;

use ams_m1_kernel::census::{FourthSlotFilter, ThirdSlotFilter};
use ams_m1_kernel::emit::json_string;
use ams_m1_kernel::engine::{Engine, EngineModes};
use ams_m1_kernel::fiber::{ContextFibers, DeepFiberDeriver};
use ams_m1_kernel::fixpoint::{EnumerationModes, right_token_label};
use ams_m1_kernel::index::SpecIndex;
use ams_m1_kernel::liveness::ProspectLiveness;
use ams_m1_kernel::model::Sym;
use ams_m1_kernel::options::WindowOptions;
use ams_m1_kernel::{cases, emit, fixpoint, guard, parse, stream};

const USAGE: &str = "usage: ams-m1-kernel spec-echo <spec>\n       ams-m1-kernel settle-cases <spec> <cases> [--features=a,b] [--candidacy-prospect] [--vote-slots-off]\n       ams-m1-kernel guard-sweep <spec>\n       ams-m1-kernel enumerate <spec> [--features=a,b] [--candidacy-prospect] [--vote-slots-off] [--deep-classes-off]\n       ams-m1-kernel liveness-cases <spec> <keys> [--features=a,b] [--candidacy-prospect] [--vote-slots-off]";

/// What a command line named, before any verb has said how many positionals it wants. The three mode flags are spelled as negations because all three modes ship on, so a plain invocation is the shipping configuration.
struct Flags<'a> {
    positionals: Vec<&'a str>,
    features: Vec<&'a str>,
    simulated_prospect: bool,
    vote_slots: bool,
    deep_classes: bool,
}

/// What a `settle-cases` invocation asked for.
struct CasesPlan<'a> {
    spec: &'a str,
    cases: &'a str,
    features: Vec<&'a str>,
    simulated_prospect: bool,
    vote_slots: bool,
}

/// What an `enumerate` invocation asked for — [`CasesPlan`]'s flag vocabulary over one positional, plus the grain, since a fixpoint is one configuration's whole answer and the configuration is named the same way.
struct EnumeratePlan<'a> {
    spec: &'a str,
    features: Vec<&'a str>,
    simulated_prospect: bool,
    vote_slots: bool,
    deep_classes: bool,
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
            let [path] = rest else {
                return usage();
            };
            guard_sweep(path)
        }
        "enumerate" => {
            let Some(plan) = plan_enumerate(rest) else {
                return usage();
            };
            enumerate(&plan)
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

/// The flag scan every verb shares, or `None` for anything the contract does not spell. `grain` says whether this verb spells `--deep-classes-off` at all; a verb that does not takes it as the unknown flag it is.
///
/// An empty `--features=` is a usage error rather than a no-feature configuration: the harness omits the flag entirely when nothing is active, so an empty value means the two sides' flag sets have drifted and saying so is more useful than guessing.
fn scan_flags(rest: &[String], grain: bool) -> Option<Flags<'_>> {
    let mut positionals: Vec<&str> = Vec::new();
    let mut features: Option<Vec<&str>> = None;
    let mut simulated_prospect = true;
    let mut vote_slots = true;
    let mut deep_classes = true;
    for argument in rest {
        if argument == "--candidacy-prospect" {
            simulated_prospect = false;
        } else if argument == "--vote-slots-off" {
            vote_slots = false;
        } else if grain && argument == "--deep-classes-off" {
            deep_classes = false;
        } else if let Some(list) = argument.strip_prefix("--features=") {
            if list.is_empty() || features.is_some() {
                return None;
            }
            features = Some(list.split(',').collect());
        } else if argument.starts_with('-') {
            return None;
        } else {
            positionals.push(argument.as_str());
        }
    }
    Some(Flags {
        positionals,
        features: features.unwrap_or_default(),
        simulated_prospect,
        vote_slots,
        deep_classes,
    })
}

fn plan_cases(rest: &[String]) -> Option<CasesPlan<'_>> {
    let flags = scan_flags(rest, false)?;
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

fn plan_enumerate(rest: &[String]) -> Option<EnumeratePlan<'_>> {
    let flags = scan_flags(rest, true)?;
    let [spec] = flags.positionals.as_slice() else {
        return None;
    };
    Some(EnumeratePlan {
        spec,
        features: flags.features,
        simulated_prospect: flags.simulated_prospect,
        vote_slots: flags.vote_slots,
        deep_classes: flags.deep_classes,
    })
}

fn plan_liveness(rest: &[String]) -> Option<LivenessPlan<'_>> {
    let flags = scan_flags(rest, false)?;
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
    let index = read_index(plan.spec)?;
    let features = feature_syms(&index, plan.spec, &plan.features)?;
    let product = fixpoint::enumerate_transitions(
        &index,
        &features,
        EnumerationModes {
            simulated_prospect: plan.simulated_prospect,
            vote_slots: plan.vote_slots,
            deep_classes: plan.deep_classes,
        },
    )
    .map_err(|complaint| format!("{}: {complaint}", plan.spec))?;
    let text = stream::emit_transitions(&index, &product)
        .map_err(|complaint| format!("{}: {complaint}", plan.spec))?;
    write_out(&text)
}

fn guard_sweep(path: &str) -> Result<(), String> {
    let index = read_index(path)?;
    let lines = guard::sweep(&index).map_err(|error| format!("{path}: {error}"))?;
    write_lines(&lines)
}

/// One key file answered through one engine in file order — the liveness-grain differential's whole kernel side.
///
/// Everything the answers are read out of is built once and shared: one engine, one liveness probe, one filter per depth, one deriver. That is not a shortcut around a cold comparison but the arrangement the fixpoint itself runs in, and the memos it makes possible are what keep a full sweep affordable.
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
    /// The probe is lent only where the engine's own modes make a deep world, which is exactly where `third_slot_filter` builds a `_ProspectLiveness` at all — with both flags off the filters are the own-rune chain census and nothing else, and lending a probe there would answer a question Python never asks. The deriver, by contrast, answers whatever it is asked: a `fibers` key is only ever generated for a live letter-letter context of a deep world, and which contexts those are is the caller's knowledge.
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

/// The probe a filter is lent in this world, `third_slot_filter`'s `liveness = _liveness_probe(spec, probe) if probe.simulated_prospect or probe.vote_slots else None`.
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
    let mut out = [Sym(0); N];
    for (seat, name) in names.iter().enumerate() {
        out[seat] = index
            .sym_of(name)
            .filter(|rune| index.is_modeled(*rune))
            .ok_or_else(|| format!("{name} is not a rune this spec models"))?;
    }
    Ok(out)
}

/// One context's fiber partition in the shape the Python emitter writes with `json.dumps(..., separators=(",", ":"))`: the boundary options, then one object per fiber carrying its members, its fourth-slot verdict and its r4 groups.
///
/// Every collection rides in the deriver's own order — boundary options in static-list order, fibers in first-member-encountered order, members as collected, r4 groups in option-pipeline order — because that order is what the two sides are being compared on. A dead fourth spells its groups as the empty list.
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

/// One token list as a compact JSON array of `table._right_token_label` labels.
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
        })
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
    }

    /// The grain flag belongs to `enumerate` alone: nothing else writes rows, so nothing else has a grain to name, and a verb that does not spell a flag treats it as the unknown flag it is.
    #[test]
    fn only_enumerate_spells_the_grain_flag() {
        assert!(cased(&["spec.json", "cases.txt", "--deep-classes-off"]).is_none());
        assert!(livened(&["spec.json", "keys.txt", "--deep-classes-off"]).is_none());
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
    }
}
