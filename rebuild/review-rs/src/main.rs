//! The `ams-review` binary.
//!
//! - `ams-review version` writes the wire format and the kernel's spec dump format to stdout, one tab-separated `name value` line each ([`ams_review::version_text`]).
//! - `ams-review stage STAGE --init=PATH FILE` runs one stage of the stage fixtures over the dump `FILE` and writes one `{"key", "value"}` line per answer to stdout, each value in the typed encoding. The stages are `wire` ([`ams_review::wire::stage_wire`]) and `pyfmt` ([`ams_review::pyfmt::stage_pyfmt`], which reads nothing from the init). A refused input exits 1 with the reason on stderr.
//!
//! A usage mistake (no subcommand, an unknown one, an argument a subcommand does not take, or an argument that is not valid Unicode) exits 2 with the usage on stderr.

#![forbid(unsafe_code)]

use std::io::Write;
use std::path::Path;
use std::process::ExitCode;

const USAGE: &str =
    "usage: ams-review version\n       ams-review stage {wire,pyfmt} --init=PATH FILE";

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
        ["stage", name @ ("wire" | "pyfmt"), init, file] => match init.strip_prefix("--init=") {
            Some(init) => stage(name, init, file),
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

fn stage(name: &str, init: &str, file: &str) -> ExitCode {
    let mut stdout = std::io::BufWriter::new(std::io::stdout().lock());
    let (init, file) = (Path::new(init), Path::new(file));
    let result = match name {
        "wire" => {
            ams_review::wire::stage_wire(init, file, &mut stdout).map_err(|error| error.to_string())
        }
        _ => ams_review::pyfmt::stage_pyfmt(file, &mut stdout).map_err(|error| error.to_string()),
    }
    .and_then(|()| {
        stdout
            .flush()
            .map_err(|error| format!("cannot write the stage: {error}"))
    });
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("ams-review: stage {name}: {error}");
            ExitCode::from(1)
        }
    }
}

fn usage() -> ExitCode {
    eprintln!("{USAGE}");
    ExitCode::from(2)
}
