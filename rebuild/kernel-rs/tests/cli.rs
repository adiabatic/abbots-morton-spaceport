//! The binary as its callers see it: argument vector in, exit status and two streams out.
//!
//! The unit suites reach the same code as functions, which is where the fixpoint and the fan-out are proved; what only a process can prove is the wiring around them — that `enumerate` and `enumerate-configs` really do write the same bytes to two different places, that a clean fan-out says nothing at all, that `--timings` reaches stderr in the shape `cycle_timings.py` parses, and that a refused command line is a 2 while a refused run is a 1. The spec is the same four-family fixture the unit suites read, written to disk because a path is all the binary takes.

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use ams_m1_kernel::index::fixtures;

/// The binary this crate builds, handed over by Cargo, so the tests run whatever was just compiled rather than whatever is on the path.
const KERNEL: &str = env!("CARGO_BIN_EXE_ams-m1-kernel");

/// The two configurations the fixture can tell apart, and the flag one `enumerate` names each by: it unlocks a `qsMay` entry under `ss03` and nothing under nothing.
const CONFIGS: [(&str, Option<&str>); 2] = [("default", None), ("ss03", Some("--features=ss03"))];

/// A scratch directory of this test's own, cleared first so nothing a previous run left can stand in for what this one was supposed to write. It lives under `target/`, which is gitignored, rather than in the system temp directory.
fn scratch(name: &str) -> PathBuf {
    let directory = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("target/test-scratch")
        .join(name);
    let _ = std::fs::remove_dir_all(&directory);
    std::fs::create_dir_all(&directory).expect("the scratch directory is makeable");
    directory
}

/// The fixture spec on disk, which is the only form the binary accepts one in.
fn spec_at(root: &Path) -> PathBuf {
    let path = root.join("spec.json");
    std::fs::write(&path, fixtures::mini_dump()).expect("the scratch directory takes a spec");
    path
}

/// One path as the binary would be handed it.
fn word(path: &Path) -> &str {
    path.to_str().expect("a scratch path is Unicode")
}

fn run(arguments: &[&str]) -> Output {
    Command::new(KERNEL)
        .args(arguments)
        .output()
        .expect("the binary this crate just built runs")
}

/// The complaint a failed run made, for the assertions that read it.
fn complaint(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

/// One `[t]` line's phase, split the way `^\[t\] (.+?) (\d+(?:\.\d+)?)s$` splits it and panicking on a line that shape does not match — hand-rolled rather than matched, because this crate carries serde_json and nothing else, and a test that added a regex dependency would be paying for the assertion in build time forever.
fn timing_phase(line: &str) -> &str {
    let body = line
        .strip_prefix("[t] ")
        .unwrap_or_else(|| panic!("a timings line starts with the marker: {line}"));
    let body = body
        .strip_suffix('s')
        .unwrap_or_else(|| panic!("a timings line ends in seconds: {line}"));
    let (phase, seconds) = body
        .rsplit_once(' ')
        .unwrap_or_else(|| panic!("a timings line names a phase and a duration: {line}"));
    assert!(!phase.is_empty(), "a timings line names a phase: {line}");
    let (whole, fraction) = seconds
        .split_once('.')
        .map_or((seconds, None), |(whole, rest)| (whole, Some(rest)));
    assert!(digits(whole), "a duration starts with digits: {line}");
    if let Some(fraction) = fraction {
        assert!(digits(fraction), "and its decimal is digits too: {line}");
    }
    phase
}

fn digits(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|byte| byte.is_ascii_digit())
}

/// The exit bar itself, through two processes rather than two calls: what a fan-out files under a configuration's name is what `enumerate` writes to stdout for that configuration, at one thread and at more threads than there are configurations.
#[test]
fn a_fan_out_files_what_one_enumeration_writes_to_stdout() {
    let root = scratch("cli-identity");
    let spec = spec_at(&root);
    for threads in ["1", "4"] {
        let outdir = root.join(format!("at-{threads}"));
        let fanned = run(&[
            "enumerate-configs",
            word(&spec),
            word(&outdir),
            "--configs=default,ss03",
            &format!("--threads={threads}"),
        ]);
        assert!(
            fanned.status.success(),
            "the fan-out answers: {}",
            complaint(&fanned)
        );
        for (token, features) in CONFIGS {
            let mut arguments = vec!["enumerate", word(&spec)];
            arguments.extend(features);
            let one = run(&arguments);
            assert!(
                one.status.success(),
                "and so does one enumeration: {}",
                complaint(&one)
            );
            let filed = std::fs::read(outdir.join(format!("transitions-{token}.ndjson")))
                .expect("every named configuration left a file behind");
            assert_eq!(
                one.stdout, filed,
                "{token} at {threads} threads is not the bytes one enumeration writes"
            );
        }
    }
}

/// A fan-out that was not asked to time itself says nothing on either stream, which is what lets the identity harness read any stderr on a clean exit as a failure.
#[test]
fn a_clean_fan_out_says_nothing_at_all() {
    let root = scratch("cli-silence");
    let spec = spec_at(&root);
    let output = run(&[
        "enumerate-configs",
        word(&spec),
        word(&root.join("streams")),
        "--configs=default,ss03",
    ]);
    assert!(output.status.success(), "{}", complaint(&output));
    assert!(output.stdout.is_empty(), "the answer here is the files");
    assert!(output.stderr.is_empty(), "and nothing else is said");
}

/// The `--timings` lines are the shape `cycle_timings.py` recovers a child's phases from, and they arrive in the order the command line named its configurations however wide the run was.
#[test]
fn the_timings_lines_are_the_shape_the_cycle_parses_in_the_order_named() {
    let root = scratch("cli-timings");
    let spec = spec_at(&root);
    let output = run(&[
        "enumerate-configs",
        word(&spec),
        word(&root.join("streams")),
        "--configs=default,ss03",
        "--threads=4",
        "--timings",
    ]);
    assert!(output.status.success(), "{}", complaint(&output));
    assert!(output.stdout.is_empty(), "the answer is still the files");
    let stderr = String::from_utf8(output.stderr).expect("the timings are text");
    let phases: Vec<&str> = stderr.lines().map(timing_phase).collect();
    assert_eq!(
        phases,
        [
            "spec_parse",
            "enumerate[default]",
            "emit[default]",
            "enumerate[ss03]",
            "emit[ss03]",
            "enumerate_total"
        ]
    );
}

/// A command line the verb will not spell exits 2 without reading anything, and a configuration the spec will not answer exits 1 having read it — the difference between a caller that asked wrongly and a caller that asked for something this spec has not got.
#[test]
fn a_malformed_command_line_is_a_two_and_an_unanswerable_one_is_a_one() {
    let root = scratch("cli-refusals");
    let spec = spec_at(&root);
    let outdir = root.join("streams");
    for tail in [
        vec!["--configs=+ss03"],
        vec!["--configs=ss03++ss05"],
        vec!["--configs=ss03+"],
        vec!["--configs=default", "--threads=+3"],
    ] {
        let mut arguments = vec!["enumerate-configs", word(&spec), word(&outdir)];
        arguments.extend(&tail);
        let output = run(&arguments);
        assert_eq!(
            output.status.code(),
            Some(2),
            "{tail:?} is a usage error: {}",
            complaint(&output)
        );
    }
    let unknown = run(&[
        "enumerate-configs",
        word(&spec),
        word(&outdir),
        "--configs=ss05",
    ]);
    assert_eq!(unknown.status.code(), Some(1));
    assert!(
        complaint(&unknown).contains("ss05"),
        "the complaint names the feature this spec never mentions: {}",
        complaint(&unknown)
    );
}

/// A directory globbed after a clean exit holds this run's answer and nothing else: a stream left by a configuration this run was not asked about is gone, and anything that is not a stream is where its owner left it.
#[test]
fn a_clean_fan_out_sweeps_the_streams_it_did_not_name() {
    let root = scratch("cli-sweep");
    let spec = spec_at(&root);
    let outdir = root.join("streams");
    std::fs::create_dir_all(&outdir).expect("the output directory can pre-exist");
    let stale = outdir.join("transitions-zz.ndjson");
    std::fs::write(&stale, "a configuration nobody asked about\n").expect("the directory takes it");
    let bystander = outdir.join("manifest.json");
    std::fs::write(&bystander, "{}\n").expect("and something that is not a stream");
    let output = run(&[
        "enumerate-configs",
        word(&spec),
        word(&outdir),
        "--configs=default",
    ]);
    assert!(output.status.success(), "{}", complaint(&output));
    assert!(
        !stale.exists(),
        "the unnamed configuration's stream is gone"
    );
    assert!(bystander.exists(), "and nothing else was touched");
    assert!(outdir.join("transitions-default.ndjson").exists());
}

/// A seat that cannot write its stream fails the whole run, and the complaint is the earliest-seated failure rather than whichever worker got there first — the first configuration is always claimed, so a run with every seat blocked reports that one every time.
#[test]
fn a_seat_that_cannot_write_fails_the_run_naming_the_earliest_one() {
    let root = scratch("cli-blocked");
    let spec = spec_at(&root);
    let outdir = root.join("streams");
    for (token, _) in CONFIGS {
        std::fs::create_dir_all(outdir.join(format!("transitions-{token}.ndjson")))
            .expect("a directory can occupy a stream's path");
    }
    let output = run(&[
        "enumerate-configs",
        word(&spec),
        word(&outdir),
        "--configs=default,ss03",
        "--threads=2",
    ]);
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty(), "a failed run wrote no answer");
    let said = complaint(&output);
    assert!(
        said.contains("transitions-default.ndjson"),
        "the earliest seat is the one named: {said}"
    );
    assert!(
        !said.contains("transitions-ss03.ndjson"),
        "and it is the only one named: {said}"
    );
}
