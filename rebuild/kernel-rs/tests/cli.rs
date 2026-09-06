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

/// The guard answers one named configuration through the same verb that answers the powerset, in the same shape, `default` among the names so the no-feature configuration is askable, and refuses what its pinned world cannot honor: a token that is not a configuration's canonical spelling is the usage error `--configs=` makes of it, `--features=` is outside this verb's vocabulary, and a mode flag is a usage error too, because the guard's modes are `guard.rs`'s to pin. A feature the spec never mentions is a refused run rather than a quiet default, exactly as `settle-cases` refuses it.
#[test]
fn a_guard_sweep_answers_one_configuration_and_refuses_a_world_flag() {
    let root = scratch("cli-guard");
    let spec = spec_at(&root);
    let quantified = run(&["guard-sweep", word(&spec)]);
    assert!(
        quantified.status.success(),
        "the quantified sweep answers: {}",
        complaint(&quantified)
    );
    for token in ["default", "ss03"] {
        let under = run(&["guard-sweep", word(&spec), &format!("--config={token}")]);
        assert!(
            under.status.success(),
            "and so does {token}'s: {}",
            complaint(&under)
        );
        assert!(
            under.stderr.is_empty(),
            "a clean sweep says nothing on stderr"
        );
        assert_eq!(
            under.stdout.iter().filter(|byte| **byte == b'\n').count(),
            quantified
                .stdout
                .iter()
                .filter(|byte| **byte == b'\n')
                .count(),
            "{token}'s surface has the quantified surface's rows"
        );
    }
    for tail in [
        vec!["--config="],
        vec!["--config=ss03+"],
        vec!["--config=default", "--config=ss03"],
        vec!["--features=ss03"],
        vec!["--candidacy-prospect"],
        vec!["--vote-slots-off"],
        vec!["--deep-classes-off"],
    ] {
        let mut arguments = vec!["guard-sweep", word(&spec)];
        arguments.extend(&tail);
        let output = run(&arguments);
        assert_eq!(
            output.status.code(),
            Some(2),
            "{tail:?} is a usage error: {}",
            complaint(&output)
        );
    }
    let unknown = run(&["guard-sweep", word(&spec), "--config=ss05"]);
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

/// The table build as its caller sees it: three files per configuration under the directory it named, one digest line per configuration on stdout in the order they were named, and the stamp the command line gave riding the windows head where `read_windows` will look for it.
#[test]
fn a_table_build_files_three_artifacts_and_answers_one_digest_per_configuration() {
    let root = scratch("cli-tables");
    let spec = spec_at(&root);
    let outdir = root.join("tables");
    let output = run(&[
        "build-tables",
        word(&spec),
        word(&outdir),
        "--configs=default,ss03",
        "--inputs=cli-stamp",
        "--threads=2",
    ]);
    assert!(output.status.success(), "{}", complaint(&output));
    assert!(output.stderr.is_empty(), "a clean build says nothing");
    let answers: Vec<String> = String::from_utf8(output.stdout)
        .expect("the digests are text")
        .lines()
        .map(str::to_owned)
        .collect();
    assert_eq!(answers.len(), 2);
    for (answer, (token, _)) in answers.iter().zip(CONFIGS) {
        assert!(
            answer.starts_with(&format!("{{\"config\":\"{token}\",\"digest\":\"")),
            "the answer names its configuration in the order it was asked for: {answer}"
        );
        for family in ["settlement", "treaties"] {
            let path = outdir.join(format!("{family}-{token}.tsv"));
            let text = std::fs::read_to_string(&path).expect("every family lands");
            assert!(text.starts_with(&format!(
                "# {} table, config {token}\n",
                family_word(family)
            )));
        }
        let windows = std::fs::read_to_string(outdir.join(format!("windows-{token}.tsv")))
            .expect("and so does the enumeration");
        let head = windows.lines().next().expect("the head line");
        assert!(head.starts_with("# ams-m1-windows/2\t"), "{head}");
        assert!(head.contains("\"inputs\":\"cli-stamp\""), "{head}");
        assert_eq!(
            windows.lines().nth(1),
            Some("input\tleft\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome")
        );
    }
}

/// The configuration delta is invisible in the artifacts: a table build reading `default`'s memo for the other configurations files the same three artifacts per configuration, byte for byte, as one told to enumerate every configuration from scratch, and answers the same digests.
#[test]
fn a_seeded_table_build_files_the_bytes_a_from_scratch_one_files() {
    let root = scratch("cli-config-seed");
    let spec = spec_at(&root);
    let seeded = root.join("seeded");
    let scratch_built = root.join("scratch");
    let mut answers: Vec<String> = Vec::new();
    for (outdir, extra) in [(&seeded, None), (&scratch_built, Some("--config-seed-off"))] {
        let mut arguments = vec![
            "build-tables",
            word(&spec),
            word(outdir),
            "--configs=default,ss03",
            "--inputs=cli-stamp",
            "--threads=2",
        ];
        arguments.extend(extra);
        let output = run(&arguments);
        assert!(output.status.success(), "{}", complaint(&output));
        answers.push(String::from_utf8(output.stdout).expect("the digests are text"));
    }
    assert_eq!(answers[0], answers[1]);
    for (token, _) in CONFIGS {
        for family in ["settlement", "treaties", "windows"] {
            let name = format!("{family}-{token}.tsv");
            assert_eq!(
                std::fs::read(seeded.join(&name)).expect("the seeded build filed it"),
                std::fs::read(scratch_built.join(&name)).expect("and so did the other"),
                "{name}"
            );
        }
    }
}

/// The seed across builds through the binary: a build over an edited spec, reading the previous build's memo files with the edited rune named, files the bytes a from-scratch build of the edited spec files — for every configuration, the deltas included — and leaves memo files of its own under the stamp it was handed.
#[test]
fn a_build_seeded_from_the_previous_memo_files_the_bytes_a_from_scratch_one_files() {
    let root = scratch("cli-memo-seed");
    let before = spec_at(&root);
    let after = root.join("edited.json");
    let refusal = format!(
        "\"refuse\":{}",
        fixtures::seq(&[&fixtures::record(&[
            ("kind", "\"refuse\""),
            (
                "provenance",
                &fixtures::names(&["qsTea.yaml", "policy.refuse[0]"])
            ),
        ])])
    );
    let edited = fixtures::mini_dump().replacen(&refusal, "\"refuse\":[]", 1);
    assert_ne!(
        edited,
        fixtures::mini_dump(),
        "the edit lands on qsTea's refusal"
    );
    std::fs::write(&after, edited).expect("the edited spec writes");
    let previous = root.join("previous");
    let output = run(&[
        "build-tables",
        word(&before),
        word(&previous),
        "--configs=default,ss03",
        "--inputs=cli-stamp",
        "--memo-stamp=before",
    ]);
    assert!(output.status.success(), "{}", complaint(&output));
    for (token, _) in CONFIGS {
        assert!(previous.join(format!("memo-{token}.tsv")).is_file());
    }
    let seeded = root.join("seeded");
    let output = run(&[
        "build-tables",
        word(&after),
        word(&seeded),
        "--configs=default,ss03",
        "--inputs=cli-stamp",
        &format!("--seed={}", word(&previous)),
        "--edited=qsTea",
        "--memo-stamp=after",
        "--timings",
    ]);
    assert!(output.status.success(), "{}", complaint(&output));
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    let phases: Vec<&str> = stderr.lines().map(timing_phase).collect();
    assert!(phases.contains(&"memo[default]"), "{phases:?}");
    let scratch_built = root.join("scratch");
    let output = run(&[
        "build-tables",
        word(&after),
        word(&scratch_built),
        "--configs=default,ss03",
        "--inputs=cli-stamp",
    ]);
    assert!(output.status.success(), "{}", complaint(&output));
    for (token, _) in CONFIGS {
        for family in ["settlement", "treaties", "windows"] {
            let name = format!("{family}-{token}.tsv");
            assert_eq!(
                std::fs::read(seeded.join(&name)).expect("the seeded build filed it"),
                std::fs::read(scratch_built.join(&name)).expect("and so did the other"),
                "{name}"
            );
        }
        assert!(seeded.join(format!("memo-{token}.tsv")).is_file());
        assert!(!scratch_built.join(format!("memo-{token}.tsv")).exists());
    }
    let output = run(&[
        "build-tables",
        word(&after),
        word(&root.join("misuse")),
        "--configs=default",
        "--inputs=cli-stamp",
        "--edited=qsTea",
    ]);
    assert_eq!(
        output.status.code(),
        Some(2),
        "--edited= without --seed= is a usage error"
    );
    let output = run(&[
        "build-tables",
        word(&after),
        word(&root.join("misuse")),
        "--configs=default",
        "--inputs=cli-stamp",
        "--moved-classes=halves-that-exit-at-x-height",
    ]);
    assert_eq!(
        output.status.code(),
        Some(2),
        "--moved-classes= without --seed= is a usage error too"
    );
}

/// The word each TSV's own comment line uses for itself.
fn family_word(family: &str) -> &str {
    match family {
        "settlement" => "settlement",
        _ => "treaty",
    }
}

/// The build's own phases, which the cycle reads the same way it reads a stream run's, and which name the fold rather than the emitter now that there is nothing to emit.
#[test]
fn a_timed_table_build_names_the_enumerate_and_fold_phases_per_configuration() {
    let root = scratch("cli-tables-timings");
    let spec = spec_at(&root);
    let output = run(&[
        "build-tables",
        word(&spec),
        word(&root.join("tables")),
        "--configs=default,ss03",
        "--inputs=cli-stamp",
        "--threads=2",
        "--timings",
    ]);
    assert!(output.status.success(), "{}", complaint(&output));
    let stderr = String::from_utf8(output.stderr).expect("the timings are text");
    let phases: Vec<&str> = stderr.lines().map(timing_phase).collect();
    assert_eq!(
        phases,
        [
            "spec_parse",
            "enumerate[default]",
            "fold[default]",
            "enumerate[ss03]",
            "fold[ss03]",
            "tables_total"
        ]
    );
}

/// The stamp is required rather than defaulted, because a serialized enumeration is trusted or refused on it; the rest of the verb's vocabulary is refused the same way the fan-out's is.
#[test]
fn a_table_build_without_a_stamp_is_a_usage_error() {
    let root = scratch("cli-tables-refusals");
    let spec = spec_at(&root);
    let outdir = root.join("tables");
    for tail in [
        vec!["--configs=default"],
        vec!["--configs=default", "--inputs="],
        vec!["--inputs=stamp"],
        vec!["--configs=default", "--inputs=a", "--inputs=b"],
        vec!["--configs=default", "--inputs=a", "--features=ss03"],
    ] {
        let mut arguments = vec!["build-tables", word(&spec), word(&outdir)];
        arguments.extend(&tail);
        let output = run(&arguments);
        assert_eq!(
            output.status.code(),
            Some(2),
            "{tail:?} is a usage error: {}",
            complaint(&output)
        );
    }
}
