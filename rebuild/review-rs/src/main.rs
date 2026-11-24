//! The `ams-review` binary.
//!
//! - `ams-review build --input=PATH` runs the review corpus build the build input at `PATH` describes ([`ams_review::corpus::build`]), reading the audit through the descriptors it inherits, with its console lines on stderr and one result line on stdout, written last ([`ams_review::corpus::build_result`]): it exits 0 after a `done` line and 1 after an `exit` or `error` line. One thread blocks on reading stdin, and when stdin ends the process exits 1 at once, committing nothing further.
//! - `ams-review version` writes the units init's format, the build input's format, the kernel's spec dump format, the linked HarfBuzz version and the linked zlib version to stdout, one tab-separated `name value` line each ([`ams_review::version_text`]).
//!
//! A usage mistake (no subcommand, an unknown one, an argument a subcommand does not take, or an argument that is not valid Unicode) exits 2 with the usage on stderr.

#![forbid(unsafe_code)]

use std::io::{Read, Write};
use std::path::Path;
use std::process::ExitCode;

const USAGE: &str = "usage: ams-review version\n       ams-review build --input=PATH";

fn main() -> ExitCode {
    let Ok(arguments) = std::env::args_os()
        .skip(1)
        .map(|argument| argument.into_string())
        .collect::<Result<Vec<String>, _>>()
    else {
        return usage();
    };
    match arguments
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["version"] => version(),
        ["build", input] => match input.strip_prefix("--input=") {
            Some(input) => build(input),
            None => usage(),
        },
        _ => usage(),
    }
}

fn version() -> ExitCode {
    let mut stdout = std::io::stdout().lock();
    match stdout
        .write_all(ams_review::version_text().as_bytes())
        .and_then(|()| stdout.flush())
    {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("ams-review: cannot write the version: {error}");
            ExitCode::from(1)
        }
    }
}

fn build(input: &str) -> ExitCode {
    let watch = std::thread::Builder::new()
        .name("stdin-watch".to_owned())
        .spawn(|| {
            let mut stdin = std::io::stdin().lock();
            let mut buffer = [0u8; 256];
            loop {
                match stdin.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(_) => {}
                    Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
                    Err(_) => break,
                }
            }
            eprintln!("ams-review: build: the standard input closed, so the build stops");
            std::process::exit(1);
        });
    if let Err(error) = watch {
        eprintln!("ams-review: build: cannot watch the standard input: {error}");
        return ExitCode::from(1);
    }
    let result = ams_review::corpus::build_result(Path::new(input), &mut std::io::stderr());
    let done = result["op"] == "done";
    let mut stdout = std::io::stdout().lock();
    if let Err(error) = writeln!(stdout, "{result}").and_then(|()| stdout.flush()) {
        eprintln!("ams-review: build: cannot write the result: {error}");
        return ExitCode::from(1);
    }
    if done {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    }
}

fn usage() -> ExitCode {
    eprintln!("{USAGE}");
    ExitCode::from(2)
}
