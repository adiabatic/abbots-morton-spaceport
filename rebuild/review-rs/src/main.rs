//! The `ams-review` binary.
//!
//! - `ams-review version` writes the wire format and the kernel's spec dump format to stdout, one tab-separated `name value` line each ([`ams_review::version_text`]).
//!
//! A usage mistake (no subcommand, an unknown one, an argument a subcommand does not take, or an argument that is not valid Unicode) exits 2 with the usage on stderr.

#![forbid(unsafe_code)]

use std::io::Write;
use std::process::ExitCode;

const USAGE: &str = "usage: ams-review version";

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

fn usage() -> ExitCode {
    eprintln!("{USAGE}");
    ExitCode::from(2)
}
