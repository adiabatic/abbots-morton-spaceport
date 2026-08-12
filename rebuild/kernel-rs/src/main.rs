//! `ams-m1-kernel` — the Rust reimplementation of the M1 settlement kernel (tracker issue #40). Today it does the first piece of that job and nothing else: it reads an `ams-m1-spec/1` dump into the interned model the settlement work will be written against, and echoes the model back out in canonical form (sub-issue #42, the ingest step).
//!
//! **`rebuild/pipeline/kernel_io.py` is the binding contract.** Its module and function docstrings define both halves of the boundary, and this crate is measured against them rather than the other way around: the dump is whatever `kernel_io.spec_json` writes, the strictness is whatever `kernel_io.spec_of` enforces, and where this crate and that module disagree, that module is right. `bench-the-rebuild/RUST-PORT-PLAN.md` carries the design facts behind the port — chiefly that the packing, not the language, is the win, and that the standard SipHash hasher beat the finalizer-less fast hasher that a first pass reached for.
//!
//! **A change to `rebuild/pipeline/model.py` is a cross-group coordination event, and it lands on this crate too.** The Python codec is driven by `dataclasses.fields`, so a new field rides the dump with no edit there; this crate spells its field sets by hand and will therefore refuse the new dump rather than silently drop the field. `make kernel-parity` is what catches the lag, and it catches it as a byte diff on the very next run.
//!
//! Three make targets drive the crate from the repo root: `make kernel-build` compiles the release binary the parity harness runs, `make kernel-check` is the crate's own gate (`cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`), and `make kernel-parity` echoes the live alphabet and every rung of the nested ladder through the binary and compares the bytes against Python.
//!
//! The CLI is two positional arguments and no argument parser: `ams-m1-kernel spec-echo <path>` writes the canonical dump plus one newline to stdout and exits 0. stdout carries that and nothing else, ever. A usage mistake — wrong argument count, wrong verb, an argument that is not valid Unicode — exits 2; a file that cannot be read, parsed, or validated exits 1 with a one-line complaint on stderr.

#![forbid(unsafe_code)]

use std::io::Write;
use std::process::ExitCode;

use ams_m1_kernel::{emit, parse};

const USAGE: &str = "usage: ams-m1-kernel spec-echo <path>";

fn main() -> ExitCode {
    let Ok(arguments) = std::env::args_os()
        .skip(1)
        .map(std::ffi::OsString::into_string)
        .collect::<Result<Vec<String>, _>>()
    else {
        eprintln!("{USAGE}");
        return ExitCode::from(2);
    };
    let [command, path] = arguments.as_slice() else {
        eprintln!("{USAGE}");
        return ExitCode::from(2);
    };
    if command != "spec-echo" {
        eprintln!("{USAGE}");
        return ExitCode::from(2);
    }
    match spec_echo(path) {
        Ok(()) => ExitCode::SUCCESS,
        Err(complaint) => {
            eprintln!("ams-m1-kernel: {complaint}");
            ExitCode::from(1)
        }
    }
}

fn spec_echo(path: &str) -> Result<(), String> {
    let text = std::fs::read_to_string(path).map_err(|error| format!("{path}: {error}"))?;
    let spec = parse::parse_spec(&text).map_err(|error| format!("{path}: {error}"))?;
    let mut echoed = emit::emit_spec(&spec);
    echoed.push('\n');
    std::io::stdout()
        .write_all(echoed.as_bytes())
        .map_err(|error| format!("stdout: {error}"))
}
