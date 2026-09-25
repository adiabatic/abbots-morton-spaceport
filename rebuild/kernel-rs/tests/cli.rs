//! Tests of the binary through its command line: arguments in; exit status, stdout, and stderr out.
//!
//! The unit tests call the same code as functions and test the fixpoint and the fan-out there. These tests check behavior that only a process shows: that `enumerate` and `enumerate-configs` write the same bytes to different places, that a clean fan-out writes nothing to stdout or stderr, that `--timings` writes to stderr in the format `cycle_timings.py` parses, and that a usage error exits 2 while a failed run exits 1. The spec is the four-family fixture the unit tests use (`fixtures::mini_dump`), written to disk because the binary takes only a path.

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use ams_m1_kernel::index::fixtures;

/// The binary Cargo built for this crate, so the tests run what was just compiled and not whatever is on the path.
const KERNEL: &str = env!("CARGO_BIN_EXE_ams-m1-kernel");

/// The two configurations the fixture distinguishes, and the flag `enumerate` takes for each: `ss03` unlocks a `qsMay` entry, and `default` has no features.
const CONFIGS: [(&str, Option<&str>); 2] = [("default", None), ("ss03", Some("--features=ss03"))];

/// This test's own scratch directory, cleared first so that files from a previous run cannot pass for this run's output. It is under `target/`, which is gitignored.
fn scratch(name: &str) -> PathBuf {
    let directory = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("target/test-scratch")
        .join(name);
    let _ = std::fs::remove_dir_all(&directory);
    std::fs::create_dir_all(&directory).expect("the scratch directory is makeable");
    directory
}

/// Writes the fixture spec to disk, the only form in which the binary accepts one.
fn spec_at(root: &Path) -> PathBuf {
    let path = root.join("spec.json");
    std::fs::write(&path, fixtures::mini_dump()).expect("the scratch directory takes a spec");
    path
}

/// A path as a command-line argument.
fn word(path: &Path) -> &str {
    path.to_str().expect("a scratch path is Unicode")
}

fn run(arguments: &[&str]) -> Output {
    Command::new(KERNEL)
        .args(arguments)
        .output()
        .expect("the binary this crate just built runs")
}

/// A run's stderr, for the assertions that read it.
fn complaint(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

/// Returns the phase label of one `[t]` line, parsed as `console.INNER_LINE` (the pattern `cycle_timings.py` uses) parses it, but without the optional trailing fields that pattern allows. Panics on a line that does not match. The parser is hand-written because the crate's only dependency is serde_json, and adding a regex dependency would lengthen every build.
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

/// The file a fan-out writes for a configuration has the bytes `enumerate` writes to stdout for that configuration, at one thread and at more threads than there are configurations. The check runs two processes, not two function calls.
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

/// A fan-out run without `--timings` writes nothing to stdout or stderr. `kernel_exec._forward_stderr` relies on this: it treats any stderr on a clean exit without timings as a failure.
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

/// The `--timings` lines have the format `cycle_timings.py` reads a child's phases from, and they come in the order the command line named the configurations, whatever the thread count.
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

/// A command line the subcommand cannot parse exits 2 without reading anything, and a configuration the spec cannot provide exits 1 after reading the spec. The first is a caller that asked wrongly; the second asked for something this spec does not have.
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

/// The case replay echoes each question before its answer in both shapes: the full trace by default, and the settled record's seven fields under `--settled-only`. `liveness-cases` has no trace to leave out, so it rejects the flag as a usage error.
#[test]
fn a_case_replay_answers_in_either_shape_and_the_liveness_verb_refuses_the_flag() {
    let root = scratch("cli-cases");
    let spec = spec_at(&root);
    let question = "edge\t\t\t\t\t\t\t\tqsPea\tqsTea\tedge\tunknown\tunknown";
    let cases = root.join("cases.tsv");
    std::fs::write(&cases, format!("{question}\n"))
        .expect("the scratch directory takes a case file");
    let traced = run(&["settle-cases", word(&spec), word(&cases)]);
    assert_eq!(traced.status.code(), Some(0), "{}", complaint(&traced));
    let stdout = String::from_utf8_lossy(&traced.stdout);
    assert!(
        stdout.starts_with(&format!("{question}\t{{\"settled\":")),
        "{stdout}"
    );
    let settled = run(&["settle-cases", word(&spec), word(&cases), "--settled-only"]);
    assert_eq!(settled.status.code(), Some(0), "{}", complaint(&settled));
    assert_eq!(
        String::from_utf8_lossy(&settled.stdout),
        format!("{question}\tqsPea\thalf\t\t\t\t\t0\n")
    );
    let refused = run(&[
        "liveness-cases",
        word(&spec),
        word(&cases),
        "--settled-only",
    ]);
    assert_eq!(refused.status.code(), Some(2), "{}", complaint(&refused));
}

/// `guard-sweep --config=` writes one configuration's late-formation surface with as many rows as the default sweep over the feature powerset, and accepts `default` for the no-feature configuration. Each of these is a usage error (exit 2): an empty or non-canonical configuration token, as `--configs=` parses it; a repeated `--config=`; `--features=`, which this subcommand does not take; and any mode flag, because `guard.rs` fixes the guard's modes. A feature the spec never mentions fails the run (exit 1), as it does in `settle-cases`.
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

/// After a clean exit the output directory holds only this run's streams: a stream left by a configuration this run was not asked for is removed, and files that are not streams are left alone.
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

/// A configuration whose stream cannot be written fails the whole run, and the error names the earliest configuration in `--configs` order, not whichever worker failed first. The first configuration is always started, so with every stream blocked the run names it every time.
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

/// A table build writes three files per configuration in the directory it was given, prints one digest line per configuration in the order named, and writes the `--inputs=` stamp into the windows head, where `table.read_windows` reads it.
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

/// Seeding from `default`'s memo does not change the artifacts: a table build that reuses `default`'s memo for the other configurations writes the same three files per configuration, byte for byte, and the same digests as a build with `--config-seed-off`, which enumerates every configuration from scratch.
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

/// Seeding across builds: a build of an edited spec that reads the previous build's memo files, with the edited rune named, writes the bytes a from-scratch build of the edited spec writes for every configuration, and writes memo files of its own under the stamp it was given.
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
    for (arm, configs, extra, tokens) in [
        (
            "config-seed-off",
            "--configs=default,ss03",
            Some("--config-seed-off"),
            vec!["default", "ss03"],
        ),
        ("single", "--configs=default", None, vec!["default"]),
        ("without-default", "--configs=ss03", None, vec!["ss03"]),
    ] {
        let outdir = root.join(arm);
        let seed = format!("--seed={}", word(&previous));
        let mut args = vec![
            "build-tables",
            word(&after),
            word(&outdir),
            configs,
            "--inputs=cli-stamp",
            &seed,
            "--edited=qsTea",
            "--memo-stamp=after",
        ];
        args.extend(extra);
        let output = run(&args);
        assert!(output.status.success(), "{arm}: {}", complaint(&output));
        for token in tokens {
            for family in ["settlement", "treaties", "windows"] {
                let name = format!("{family}-{token}.tsv");
                assert_eq!(
                    std::fs::read(outdir.join(&name)).expect("the seeded build filed it"),
                    std::fs::read(scratch_built.join(&name)).expect("the scratch build filed it"),
                    "{arm}: {name}"
                );
            }
            assert!(outdir.join(format!("memo-{token}.tsv")).is_file());
        }
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

/// A table build's timed phases, which the cycle reads like a stream run's, including `fold.prefixes` and `fold.partition` before each `fold` line.
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
            "fold.prefixes[default]",
            "fold.partition[default]",
            "fold[default]",
            "enumerate[ss03]",
            "fold.prefixes[ss03]",
            "fold.partition[ss03]",
            "fold[ss03]",
            "tables_total"
        ]
    );
}

/// The `--inputs=` stamp is required, not defaulted, because `run_m1.serialized_tables` accepts or rejects a serialized enumeration by comparing it. An empty or repeated stamp, a missing `--configs=`, and `--features=` are usage errors too.
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

/// `replay-emitted` through the binary. Given as its order a table's own settlement TSV, with the marker renames the context file declares applied, it accounts for every row of the table's enumeration, whether the windows come from a file or from stdin. A context file with a line that is neither a `rename` nor a `class` record exits 1 naming the file. A command line missing one of the three files, or with a mode flag, exits 2.
#[test]
fn a_shipped_order_walk_answers_a_tables_rows_from_a_file_or_stdin() {
    let root = scratch("cli-emitted");
    let spec = spec_at(&root);
    let outdir = root.join("tables");
    let built = run(&[
        "build-tables",
        word(&spec),
        word(&outdir),
        "--configs=default,ss03",
        "--inputs=cli-stamp",
    ]);
    assert!(built.status.success(), "{}", complaint(&built));
    let table = outdir.join("settlement-ss03.tsv");
    let windows = outdir.join("windows-ss03.tsv");
    let context = root.join("context-ss03.tsv");
    std::fs::write(
        &context,
        "rename\tqsMay\tqsMay.ss03\nrename\tqsMay.noentry\tqsMay.ss03.noentry\n",
    )
    .expect("the context file is writable");
    let order = root.join("order.tsv");
    let twin = |token: &str| match token {
        "qsMay" => "qsMay.ss03".to_owned(),
        "qsMay.noentry" => "qsMay.ss03.noentry".to_owned(),
        other => other.to_owned(),
    };
    let mut renamed = String::new();
    for (number, line) in std::fs::read_to_string(&table)
        .expect("the table landed")
        .lines()
        .enumerate()
    {
        if number < 2 {
            renamed.push_str(line);
        } else {
            let fields: Vec<String> = line
                .split('\t')
                .enumerate()
                .map(|(column, field)| {
                    if column < 7 {
                        field
                            .split(' ')
                            .map(twin)
                            .collect::<Vec<String>>()
                            .join(" ")
                    } else {
                        field.to_owned()
                    }
                })
                .collect();
            renamed.push_str(&fields.join("\t"));
        }
        renamed.push('\n');
    }
    std::fs::write(&order, renamed).expect("the order file is writable");
    let rows = std::fs::read_to_string(&windows)
        .expect("the enumeration landed")
        .lines()
        .count()
        - 2;
    let table_flag = format!("--table={}", word(&table));
    let order_flag = format!("--order={}", word(&order));
    let context_flag = format!("--context={}", word(&context));
    let output = run(&[
        "replay-emitted",
        word(&windows),
        "--config=ss03",
        &table_flag,
        &order_flag,
        &context_flag,
    ]);
    assert!(output.status.success(), "{}", complaint(&output));
    assert!(output.stderr.is_empty(), "a clean walk says nothing");
    assert_eq!(
        String::from_utf8_lossy(&output.stdout),
        format!("{{\"config\":\"ss03\",\"rows\":{rows},\"expanded\":0}}\n")
    );

    let piped = std::process::Command::new(KERNEL)
        .args([
            "replay-emitted",
            "-",
            "--config=ss03",
            &table_flag,
            &order_flag,
            &context_flag,
            "--timings",
        ])
        .stdin(std::fs::File::open(&windows).expect("the enumeration opens"))
        .output()
        .expect("the binary runs on a pipe");
    assert!(piped.status.success(), "{}", complaint(&piped));
    assert_eq!(
        piped.stdout, output.stdout,
        "stdin and the file are one walk"
    );
    let phases: Vec<&str> = complaint(&piped)
        .lines()
        .map(timing_phase)
        .map(str::to_owned)
        .collect::<Vec<String>>()
        .leak()
        .iter()
        .map(String::as_str)
        .collect();
    assert_eq!(phases, ["replay_emitted[ss03]", "replay_emitted_total"]);

    let stray = root.join("stray.tsv");
    std::fs::write(&stray, "label\tx\n").expect("writable");
    let refused = run(&[
        "replay-emitted",
        word(&windows),
        "--config=ss03",
        &table_flag,
        &order_flag,
        &format!("--context={}", word(&stray)),
    ]);
    assert_eq!(refused.status.code(), Some(1));
    assert!(
        complaint(&refused).contains("stray.tsv: context line 1"),
        "{}",
        complaint(&refused)
    );

    let missing = run(&[
        "replay-emitted",
        word(&windows),
        "--config=ss03",
        &table_flag,
        &order_flag,
    ]);
    assert_eq!(missing.status.code(), Some(2));
    let worldly = run(&[
        "replay-emitted",
        word(&windows),
        "--config=ss03",
        &table_flag,
        &order_flag,
        &context_flag,
        "--vote-slots-off",
    ]);
    assert_eq!(worldly.status.code(), Some(2));
}

/// The replay's window memo through the binary: `--memo-dir=` writes one `replay-windows-<config>.bin` per configuration with the head `replay::MEMO_FORMAT` names. The answer lines are the bytes the same walk prints without the flag, a walk without the flag writes no memo, a timed run reports the memo phase beside the walk, and a directory the walk cannot write into fails the run naming the configuration.
#[test]
fn a_replay_with_a_memo_directory_files_one_window_memo_per_configuration() {
    let root = scratch("cli-replay-memo");
    let spec = spec_at(&root);
    let outdir = root.join("tables");
    let built = run(&[
        "build-tables",
        word(&spec),
        word(&outdir),
        "--configs=default,ss03",
        "--inputs=cli-stamp",
    ]);
    assert!(built.status.success(), "{}", complaint(&built));
    let bare = run(&[
        "replay-strings",
        word(&spec),
        word(&outdir),
        "--configs=default,ss03",
        "--horizon=3",
    ]);
    assert!(bare.status.success(), "{}", complaint(&bare));
    assert!(
        std::fs::read_dir(&outdir)
            .expect("the tables directory lists")
            .all(|entry| !entry
                .expect("an entry")
                .file_name()
                .to_string_lossy()
                .starts_with("replay-windows-")),
        "a walk without the flag files no memo"
    );
    let memos = root.join("memos");
    std::fs::create_dir_all(&memos).expect("the memo directory is makeable");
    let memo_flag = format!("--memo-dir={}", word(&memos));
    let filed = run(&[
        "replay-strings",
        word(&spec),
        word(&outdir),
        "--configs=default,ss03",
        "--horizon=3",
        &memo_flag,
        "--timings",
    ]);
    assert!(filed.status.success(), "{}", complaint(&filed));
    assert_eq!(filed.stdout, bare.stdout, "the answer lines are unchanged");
    for (token, _) in CONFIGS {
        let path = memos.join(format!("replay-windows-{token}.bin"));
        let bytes = std::fs::read(&path).expect("each configuration's memo is filed");
        let head = bytes
            .split(|byte| *byte == b'\n')
            .next()
            .expect("a head line");
        let head = std::str::from_utf8(head).expect("the head is text");
        assert!(
            head.starts_with(&format!(
                "# {}\t{{\"config\":\"{token}\",\"horizon\":3,\"rows\":",
                ams_m1_kernel::replay::MEMO_FORMAT
            )),
            "{head}"
        );
    }
    let phases: Vec<String> = complaint(&filed)
        .lines()
        .map(|line| timing_phase(line).to_owned())
        .collect();
    assert!(
        phases.iter().any(|phase| phase == "replay_memo[default]")
            && phases.iter().any(|phase| phase == "replay[default]"),
        "{phases:?}"
    );

    let blocker = root.join("blocker");
    std::fs::write(&blocker, "not a directory\n").expect("writable");
    let refused = run(&[
        "replay-strings",
        word(&spec),
        word(&outdir),
        "--configs=default,ss03",
        "--horizon=3",
        &format!("--memo-dir={}", word(&blocker.join("inside"))),
    ]);
    assert_eq!(refused.status.code(), Some(1), "{}", complaint(&refused));
    assert!(
        complaint(&refused).contains("default:") || complaint(&refused).contains("ss03:"),
        "the refusal names the configuration: {}",
        complaint(&refused)
    );
    assert!(
        refused.stdout.is_empty(),
        "nothing reaches stdout on a refusal"
    );
}

/// The replay's cache census through the binary. `--cache-census` leaves the answer lines byte for byte as the plain walk prints them. Without `--timings` it writes only `[c]` lines to stderr: for each configuration, the walk's own memo, every engine memo with the trace memo's ladder pool empty, an elimination-text size of zero, and the resident size after the walk. With `--timings`, a configuration's census lines come before its `replay[<config>]` phase line. Under a memo ceiling of a third of the walk's window count, each release reports the walk memo and the engine's memos under `release=<k>` with the resident size before and after, the walk reports its release count, and no `walk_memo` row exceeds the ceiling.
#[test]
fn a_censused_replay_writes_its_census_to_stderr_and_leaves_the_answer_alone() {
    let root = scratch("cli-replay-census");
    let spec = spec_at(&root);
    let outdir = root.join("tables");
    let built = run(&[
        "build-tables",
        word(&spec),
        word(&outdir),
        "--configs=default,ss03",
        "--inputs=cli-stamp",
    ]);
    assert!(built.status.success(), "{}", complaint(&built));
    let replay = |extra: &[&str]| {
        let mut arguments = vec![
            "replay-strings",
            word(&spec),
            word(&outdir),
            "--configs=default,ss03",
            "--horizon=3",
        ];
        arguments.extend_from_slice(extra);
        run(&arguments)
    };
    let bare = replay(&[]);
    assert!(bare.status.success(), "{}", complaint(&bare));
    assert!(bare.stderr.is_empty(), "{}", complaint(&bare));
    let censused = replay(&["--cache-census"]);
    assert!(censused.status.success(), "{}", complaint(&censused));
    assert_eq!(
        censused.stdout, bare.stdout,
        "the answer lines are unchanged"
    );
    let stderr = complaint(&censused);
    for line in stderr.lines() {
        assert!(
            line.starts_with("[c] "),
            "a census without a clock writes only census lines: {line}"
        );
    }
    for (token, _) in CONFIGS {
        for prefix in [
            format!("[c] {token} walk_memo len="),
            format!("[c] {token} trace_cache len="),
            format!("[c] {token} trace_ladders len=0 "),
            format!("[c] {token} resident_after_walk kb="),
        ] {
            assert!(
                stderr.lines().any(|line| line.starts_with(&prefix)),
                "{prefix} is in the census: {stderr}"
            );
        }
        let elimination = format!("[c] {token} elimination_text bytes=0");
        assert!(
            stderr.lines().any(|line| line == elimination),
            "{elimination} is in the census: {stderr}"
        );
    }

    let timed = replay(&["--cache-census", "--timings"]);
    assert!(timed.status.success(), "{}", complaint(&timed));
    assert_eq!(timed.stdout, bare.stdout, "the answer lines are unchanged");
    let lines: Vec<String> = complaint(&timed).lines().map(str::to_owned).collect();
    for (token, _) in CONFIGS {
        let phase = format!("replay[{token}]");
        let clocked = lines
            .iter()
            .position(|line| line.starts_with("[t] ") && timing_phase(line) == phase)
            .unwrap_or_else(|| panic!("{phase} is timed: {lines:?}"));
        let censused = lines
            .iter()
            .rposition(|line| line.starts_with(&format!("[c] {token} ")))
            .unwrap_or_else(|| panic!("{token} is censused: {lines:?}"));
        assert!(
            censused < clocked,
            "{token}'s census rides ahead of its phase: {lines:?}"
        );
    }

    let walked = answers(&bare)
        .remove("default")
        .expect("default is answered");
    let ceiling = (walked.windows / 3).max(1);
    let ceiling_flag = format!("--memo-windows={ceiling}");
    let released = replay(&["--cache-census", &ceiling_flag]);
    assert!(released.status.success(), "{}", complaint(&released));
    let stderr = complaint(&released);
    for prefix in [
        "[c] default release=1 walk_memo len=",
        "[c] default release=1 trace_cache len=",
        "[c] default release=1 resident_before_release kb=",
        "[c] default release=1 resident_after_release kb=",
    ] {
        assert!(
            stderr.lines().any(|line| line.starts_with(prefix)),
            "{prefix} is in the census: {stderr}"
        );
    }
    let releases: u64 = stderr
        .lines()
        .find_map(|line| line.strip_prefix("[c] default releases count="))
        .and_then(|count| count.parse().ok())
        .unwrap_or_else(|| panic!("the census counts default's releases: {stderr}"));
    assert!(releases >= 1, "{stderr}");
    for line in stderr
        .lines()
        .filter(|line| line.starts_with("[c] default "))
    {
        if let Some((_, rest)) = line.split_once(" walk_memo len=") {
            let len: u64 = rest
                .split(' ')
                .next()
                .and_then(|len| len.parse().ok())
                .unwrap_or_else(|| panic!("a walk_memo row states its len: {line}"));
            assert!(len <= ceiling, "the memo never passes the ceiling: {line}");
        }
    }
}

/// One replay answer line's counts.
struct Walked {
    texts: u64,
    windows: u64,
    skipped: u64,
}

/// A clean replay's answer lines, by configuration.
fn answers(output: &Output) -> std::collections::BTreeMap<String, Walked> {
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(|line| {
            let answer: serde_json::Value =
                serde_json::from_str(line).expect("an answer line is JSON");
            let count = |key: &str| {
                answer[key]
                    .as_u64()
                    .unwrap_or_else(|| panic!("{key} is a count: {line}"))
            };
            (
                answer["config"]
                    .as_str()
                    .expect("an answer names its configuration")
                    .to_owned(),
                Walked {
                    texts: count("texts"),
                    windows: count("windows"),
                    skipped: count("skipped"),
                },
            )
        })
        .collect()
}

/// The memo ceiling through the binary: a walk that releases its memo before every text reports the same texts and skipped counts as the uncapped walk for every configuration, and settles more windows. A ceiling together with a memo directory is a usage error that writes nothing to stdout and no memo file.
#[test]
fn a_replay_with_a_memo_ceiling_answers_the_texts_an_uncapped_walk_answers() {
    let root = scratch("cli-replay-ceiling");
    let spec = spec_at(&root);
    let outdir = root.join("tables");
    let built = run(&[
        "build-tables",
        word(&spec),
        word(&outdir),
        "--configs=default,ss03",
        "--inputs=cli-stamp",
    ]);
    assert!(built.status.success(), "{}", complaint(&built));
    let replay = |extra: &[&str]| {
        let mut arguments = vec![
            "replay-strings",
            word(&spec),
            word(&outdir),
            "--configs=default,ss03",
            "--horizon=4",
        ];
        arguments.extend_from_slice(extra);
        run(&arguments)
    };
    let bare = replay(&[]);
    assert!(bare.status.success(), "{}", complaint(&bare));
    let capped = replay(&["--memo-windows=1"]);
    assert!(capped.status.success(), "{}", complaint(&capped));
    let uncapped = answers(&bare);
    let released = answers(&capped);
    assert_eq!(
        uncapped.keys().collect::<Vec<_>>(),
        released.keys().collect::<Vec<_>>()
    );
    for (token, _) in CONFIGS {
        let (whole, walked) = (&uncapped[token], &released[token]);
        assert_eq!(walked.texts, whole.texts, "{token}");
        assert_eq!(walked.skipped, whole.skipped, "{token}");
        assert!(
            walked.windows > whole.windows,
            "{token} settles its windows again after each release"
        );
    }

    let memos = root.join("memos");
    std::fs::create_dir_all(&memos).expect("the memo directory is makeable");
    let both = replay(&["--memo-windows=1", &format!("--memo-dir={}", word(&memos))]);
    assert_eq!(both.status.code(), Some(2), "{}", complaint(&both));
    assert!(
        both.stdout.is_empty(),
        "nothing reaches stdout on a refusal"
    );
    assert!(
        std::fs::read_dir(&memos)
            .expect("the memo directory lists")
            .next()
            .is_none(),
        "a refused command line files nothing"
    );
}
