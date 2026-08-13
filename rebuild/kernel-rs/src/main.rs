//! `ams-m1-kernel` — the Rust reimplementation of the M1 settlement kernel (tracker issue #40). Today it does the ingest step, the settlement core and the table build's kernel half: it reads an `ams-m1-spec/1` dump into the interned model, echoes that model back out in canonical form (sub-issue #42), settles single windows against it — one case file at a time for the differential, and the whole late-formation surface for the guard (sub-issue #43) — and runs the whole table-build worklist fixpoint over one configuration, writing the transitions stream Python folds into its artifacts (sub-issue #44).
//!
//! **`rebuild/pipeline/kernel_io.py` is the binding contract for the dump, and `rebuild/pipeline/settle.py` with `rebuild/pipeline/specificity.py` for the settlement.** Their module and function docstrings define both halves of each boundary, and this crate is measured against them rather than the other way around: the dump is whatever `kernel_io.spec_json` writes, the strictness is whatever `kernel_io.spec_of` enforces, a settled window is whatever `settle.Engine.transition_trace` returns down to its raise messages, and where this crate and those modules disagree, those modules are right. `bench-the-rebuild/RUST-PORT-PLAN.md` carries the design facts behind the port — chiefly that the packing, not the language, is the win, and that the standard SipHash hasher beat the finalizer-less fast hasher that a first pass reached for.
//!
//! **A change to `rebuild/pipeline/model.py` is a cross-group coordination event, and it lands on this crate too.** The Python codec is driven by `dataclasses.fields`, so a new field rides the dump with no edit there; this crate spells its field sets by hand and will therefore refuse the new dump rather than silently drop the field. `make kernel-parity` is what catches the lag, and it catches it as a byte diff on the very next run.
//!
//! Five make targets drive the crate from the repo root: `make kernel-build` compiles the release binary the harnesses run, `make kernel-check` is the crate's own gate (`cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`), `make kernel-parity` echoes the live alphabet and every rung of the nested ladder through the binary and compares the bytes against Python, `make kernel-differential` settles every window of the golden corpus, a seeded fuzz sweep, and the exhaustive formation-guard surface on both sides and compares those bytes too, and `make kernel-fixpoint` enumerates whole configurations on both sides and compares the stream byte for byte together with the three artifacts and the digest Python folds out of it.
//!
//! The CLI is positional arguments and a hand-rolled flag scan, never an argument parser, and stdout carries the answer and nothing else, ever:
//!
//! - `ams-m1-kernel spec-echo <spec>` writes the canonical dump plus one newline.
//! - `ams-m1-kernel settle-cases <spec> <cases> [--features=a,b,…] [--candidacy-prospect] [--vote-slots-off]` replays a plain-text `ams-m1-corpus/3` case file — the harness gunzips it, because this crate carries serde_json and nothing else — through one engine in file order and writes one re-emitted case line per case. The flags name the configuration the cases were cut under: the active stylistic sets, and one flag per engine mode that is off. A window that raises a settlement error is a normal result line and never a nonzero exit.
//! - `ams-m1-kernel guard-sweep <spec>` writes the whole section 5.7 late-formation surface, one tab-separated verdict per line.
//! - `ams-m1-kernel enumerate <spec> [--features=a,b,…] --candidacy-prospect --vote-slots-off` runs one configuration's whole table-build fixpoint and writes the uncompressed `ams-m1-transitions/1` stream — the head line and one row per window. Both mode flags are required rather than defaulted this sub-issue: the fixpoint answers the pinned candidacy world alone, and the default world is sub-issue #45's, so an invocation without them is refused instead of answered in the wrong world. The harness gzips the stream, as it gunzips the case files, for the same reason.
//!
//! A usage mistake — wrong argument count, wrong verb, an unknown flag, an argument that is not valid Unicode — exits 2; a file that cannot be read, parsed, or validated, a case file this build cannot answer, an enumeration asked for in a world this build does not implement, and a window that will not settle, exit 1 with a one-line complaint on stderr.

#![forbid(unsafe_code)]

use std::io::Write;
use std::process::ExitCode;

use ams_m1_kernel::engine::{Engine, EngineModes};
use ams_m1_kernel::index::SpecIndex;
use ams_m1_kernel::model::Sym;
use ams_m1_kernel::{cases, emit, fixpoint, guard, parse, stream};

const USAGE: &str = "usage: ams-m1-kernel spec-echo <spec>\n       ams-m1-kernel settle-cases <spec> <cases> [--features=a,b] [--candidacy-prospect] [--vote-slots-off]\n       ams-m1-kernel guard-sweep <spec>\n       ams-m1-kernel enumerate <spec> [--features=a,b] --candidacy-prospect --vote-slots-off";

/// What a `settle-cases` invocation asked for. The two mode flags are spelled as negations because both modes ship on, so a plain invocation is the shipping configuration and every departure from it is visible in the command line.
struct CasesPlan<'a> {
    spec: &'a str,
    cases: &'a str,
    features: Vec<&'a str>,
    simulated_prospect: bool,
    vote_slots: bool,
}

/// What an `enumerate` invocation asked for — the same flag vocabulary as [`CasesPlan`] over one positional, since a fixpoint is one configuration's whole answer and the configuration is named the same way.
struct EnumeratePlan<'a> {
    spec: &'a str,
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

/// The flag scan for `settle-cases`, or `None` for anything the contract does not spell. An empty `--features=` is a usage error rather than a no-feature configuration: the harness omits the flag entirely when nothing is active, so an empty value means the two sides' flag sets have drifted and saying so is more useful than guessing.
fn plan_cases(rest: &[String]) -> Option<CasesPlan<'_>> {
    let mut positionals: Vec<&str> = Vec::new();
    let mut features: Option<Vec<&str>> = None;
    let mut simulated_prospect = true;
    let mut vote_slots = true;
    for argument in rest {
        if argument == "--candidacy-prospect" {
            simulated_prospect = false;
        } else if argument == "--vote-slots-off" {
            vote_slots = false;
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
    let [spec, cases] = positionals.as_slice() else {
        return None;
    };
    Some(CasesPlan {
        spec,
        cases,
        features: features.unwrap_or_default(),
        simulated_prospect,
        vote_slots,
    })
}

/// The flag scan for `enumerate`, on [`plan_cases`]'s terms and refusing the same shapes. The mode flags are scanned rather than required here, so that leaving one out is a complaint about the world this build implements — which is exit 1 and a sentence — instead of the bare usage line a malformed command line gets.
fn plan_enumerate(rest: &[String]) -> Option<EnumeratePlan<'_>> {
    let mut positionals: Vec<&str> = Vec::new();
    let mut features: Option<Vec<&str>> = None;
    let mut simulated_prospect = true;
    let mut vote_slots = true;
    for argument in rest {
        if argument == "--candidacy-prospect" {
            simulated_prospect = false;
        } else if argument == "--vote-slots-off" {
            vote_slots = false;
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
    let [spec] = positionals.as_slice() else {
        return None;
    };
    Some(EnumeratePlan {
        spec,
        features: features.unwrap_or_default(),
        simulated_prospect,
        vote_slots,
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

fn settle_cases(plan: &CasesPlan<'_>) -> Result<(), String> {
    let index = read_index(plan.spec)?;
    let features = feature_syms(&index, plan.spec, &plan.features)?;
    let mut engine = Engine::with_modes(
        &index,
        features,
        EngineModes {
            simulated_prospect: plan.simulated_prospect,
            vote_slots: plan.vote_slots,
            trace_memo: true,
            ..EngineModes::default()
        },
    );
    let text =
        std::fs::read_to_string(plan.cases).map_err(|error| format!("{}: {error}", plan.cases))?;
    let lines = cases::replay_cases(&mut engine, &text)
        .map_err(|complaint| format!("{}: {complaint}", plan.cases))?;
    write_lines(&lines)
}

/// One configuration's whole fixpoint as the uncompressed transitions stream. The world is checked before the spec is even read: an invocation missing a mode flag is asking for a build this kernel does not have, and saying so costs nothing where enumerating the wrong world would cost minutes and then a byte diff blamed on the port.
fn enumerate(plan: &EnumeratePlan<'_>) -> Result<(), String> {
    if plan.simulated_prospect || plan.vote_slots {
        return Err(
            "enumerate answers the pinned candidacy world alone — pass both --candidacy-prospect and --vote-slots-off; the shipping defaults are sub-issue #45's".to_owned(),
        );
    }
    let index = read_index(plan.spec)?;
    let features = feature_syms(&index, plan.spec, &plan.features)?;
    let product = fixpoint::enumerate_transitions(&index, &features)
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
