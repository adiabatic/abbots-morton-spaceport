//! The `ams-review` binary.
//!
//! - `ams-review version` writes the wire format, the kernel's spec dump format and the linked HarfBuzz version to stdout, one tab-separated `name value` line each ([`ams_review::version_text`]).
//! - `ams-review stage STAGE --init=PATH FILE` runs one stage of the stage fixtures over the dump `FILE` and writes one line per answer to stdout, its `key` and the stage's outputs (`value`, `rendered` and `filtered` for `explain`, or the enriched unit's fields, its mismatch lines and its primary-unit projection for `enrich`, and `value` and the deflated fragment, `packed`, for `fragment`), each in the typed encoding. The stages are `wire` ([`ams_review::wire::stage_wire`]), `pyfmt` ([`ams_review::pyfmt::stage_pyfmt`]), `yaml` ([`ams_review::pyfmt::stage_yaml`]), `shape` ([`ams_review::shape::stage_shape`]), `ink` ([`ams_review::ink::stage_ink`]), `settle` ([`ams_review::settle::stage_settle`]), `explain` ([`ams_review::explain::stage_explain`]), `subset` ([`ams_review::subset_pack::stage_subset`]), `enrich` ([`ams_review::enrich::stage_enrich`]), `scaffold` ([`ams_review::scaffold::stage_scaffold`]), `expect` ([`ams_review::expect::stage_expect`]), `schema` ([`ams_review::schema::stage_schema`]), `draft` ([`ams_review::drafts::stage_draft`]) and `fragment` ([`ams_review::fragment::stage_fragment`]); `pyfmt` and `yaml` read nothing from the init, `schema` reads only the rune schema's path, `settle` and `explain` read only the spec and the settlement modes, `subset` reads only the subset pack's path and header values, and `shape`, `ink`, `enrich`, `scaffold`, `expect`, `draft` and `fragment` refuse an init whose HarfBuzz version is not the linked one. A refused input exits 1 with the reason on stderr.
//!
//! A usage mistake (no subcommand, an unknown one, an argument a subcommand does not take, or an argument that is not valid Unicode) exits 2 with the usage on stderr.

#![forbid(unsafe_code)]

use std::io::Write;
use std::path::Path;
use std::process::ExitCode;

const USAGE: &str = "usage: ams-review version\n       ams-review stage {wire,pyfmt,yaml,shape,ink,settle,explain,subset,enrich,scaffold,expect,schema,draft,fragment} --init=PATH FILE";

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
        [
            "stage",
            name @ ("wire" | "pyfmt" | "yaml" | "shape" | "ink" | "settle" | "explain" | "subset"
            | "enrich" | "scaffold" | "expect" | "schema" | "draft" | "fragment"),
            init,
            file,
        ] => match init.strip_prefix("--init=") {
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
    let result =
        match name {
            "wire" => ams_review::wire::stage_wire(init, file, &mut stdout)
                .map_err(|error| error.to_string()),
            "pyfmt" => {
                ams_review::pyfmt::stage_pyfmt(file, &mut stdout).map_err(|error| error.to_string())
            }
            "yaml" => {
                ams_review::pyfmt::stage_yaml(file, &mut stdout).map_err(|error| error.to_string())
            }
            "shape" => ams_review::shape::stage_shape(init, file, &mut stdout)
                .map_err(|error| error.to_string()),
            "ink" => ams_review::ink::stage_ink(init, file, &mut stdout)
                .map_err(|error| error.to_string()),
            "settle" => ams_review::settle::stage_settle(init, file, &mut stdout)
                .map_err(|error| error.to_string()),
            "explain" => ams_review::explain::stage_explain(init, file, &mut stdout)
                .map_err(|error| error.to_string()),
            "subset" => ams_review::subset_pack::stage_subset(init, file, &mut stdout)
                .map_err(|error| error.to_string()),
            "enrich" => ams_review::enrich::stage_enrich(init, file, &mut stdout)
                .map_err(|error| error.to_string()),
            "scaffold" => ams_review::scaffold::stage_scaffold(init, file, &mut stdout)
                .map_err(|error| error.to_string()),
            "expect" => ams_review::expect::stage_expect(init, file, &mut stdout)
                .map_err(|error| error.to_string()),
            "schema" => ams_review::schema::stage_schema(init, file, &mut stdout)
                .map_err(|error| error.to_string()),
            "draft" => ams_review::drafts::stage_draft(init, file, &mut stdout)
                .map_err(|error| error.to_string()),
            _ => ams_review::fragment::stage_fragment(init, file, &mut stdout)
                .map_err(|error| error.to_string()),
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
