//! The `ams-review` binary.
//!
//! - `ams-review units --init=PATH --threads=N` serves the units engine over request frames on stdin and reply frames on stdout ([`ams_review::service::serve`]): it exits 0 after `stop`, and 1 after an `error` frame or when its input closes without `stop`. The engine serves the requests from a pool of `N` threads, and `N` of 0 is answered with an `error` frame.
//! - `ams-review version` writes the wire format, the build input's format, the kernel's spec dump format, the linked HarfBuzz version and the linked zlib version to stdout, one tab-separated `name value` line each ([`ams_review::version_text`]).
//! - `ams-review stage STAGE --init=PATH FILE` runs one stage of the stage fixtures over the dump `FILE` and writes one line per answer to stdout, its `key` and the stage's outputs (`value`, `rendered` and `filtered` for `explain`, or the enriched unit's fields, its mismatch lines and its primary-unit projection for `enrich`, and `value` and the deflated fragment, `packed`, for `fragment`), each in the typed encoding. The stages are `wire` ([`ams_review::wire::stage_wire`]), `pyfmt` ([`ams_review::pyfmt::stage_pyfmt`]), `yaml` ([`ams_review::pyfmt::stage_yaml`]), `shape` ([`ams_review::shape::stage_shape`]), `ink` ([`ams_review::ink::stage_ink`]), `settle` ([`ams_review::settle::stage_settle`]), `explain` ([`ams_review::explain::stage_explain`]), `subset` ([`ams_review::subset_pack::stage_subset`]), `enrich` ([`ams_review::enrich::stage_enrich`]), `scaffold` ([`ams_review::scaffold::stage_scaffold`]), `expect` ([`ams_review::expect::stage_expect`]), `schema` ([`ams_review::schema::stage_schema`]), `draft` ([`ams_review::drafts::stage_draft`]), `fragment` ([`ams_review::fragment::stage_fragment`]) and `check` ([`ams_review::check::stage_check`]); `pyfmt` and `yaml` read nothing from the init, `schema` reads only the rune schema's path, `check` reads only the vocabulary and the glyph names by character, the build stage `build-input` ([`ams_review::corpus::input::stage_build_input`]) takes a build input in place of an init, reads nothing from `FILE` and reads the audit through the descriptors it inherits, the build stage `sink` ([`ams_review::corpus::sink::stage_sink`]) reads nothing from its build input and reads the bytes its lines name from the `sink.blobs` directory beside `FILE`, the build stage `containers` ([`ams_review::corpus::gzip::stage_containers`]) reads nothing from its build input and reads its inputs from the `containers.blobs` directory beside `FILE`, the build stage `json-forms` ([`ams_review::pyfmt::stage_json_forms`]) reads nothing from its build input, the build stage `sample` ([`ams_review::corpus::random::stage_sample`]) reads nothing from its build input, the build stage `row-delta-read` ([`ams_review::corpus::rowdelta::stage_row_delta_read`]) reads nothing from its build input and reads the parts its lines name from the `row-delta-read.blobs` directory beside `FILE`, the build stage `row-delta-write` ([`ams_review::corpus::rowdelta::stage_row_delta_write`]) reads only the segment cap and the fold overrun from its build input's vocabulary and reads the files its lines name from the `row-delta-write.blobs` directory beside `FILE`, the build stage `audit-layout` ([`ams_review::corpus::audit::stage_audit_layout`]) reads the audit's configurations and header from its build input's vocabulary, the audit through the descriptors it inherits, and the parts its generated lines name from the `audit-layout.blobs` directory beside `FILE`, the build stage `stream` ([`ams_review::corpus::stream::stage_stream`]) reads the audit's configurations and header and the key-only read's width from its build input's vocabulary, the family keys from its build input, the audit through the descriptors it inherits, and the parts its generated lines name from the `stream.blobs` directory beside `FILE`, the build stage `signature-store` ([`ams_review::corpus::signatures::stage_signature_store`]) reads the store's names from its build input's vocabulary and the stores its lines name from the `signature-store.blobs` directory beside `FILE`, the build stage `signature` ([`ams_review::corpus::signatures::stage_signature`]) reads the before and after fonts and their facts through its build input's units init and refuses one whose HarfBuzz version is not the linked one, the build stage `key-only` ([`ams_review::corpus::signatures::stage_key_only`]) reads the audit's configurations and header, the key-only read's width and the store's names from its build input's vocabulary, the family keys, the stamps and the widths from its build input, the audit through the descriptors it inherits, the before and after fonts and their facts through its build input's units init, refusing one whose HarfBuzz version is not the linked one, the prior store from the dump's prior corpus as the `source.json` beside `FILE` names it, and the stores its generated lines name from the `key-only.blobs` directory beside `FILE`, the build stage `columns` ([`ams_review::corpus::columns::stage_columns`]) reads nothing from its build input, the build stage `permutation` ([`ams_review::corpus::table::stage_permutation`]) reads nothing from its build input, the build stage `merge` ([`ams_review::corpus::table::stage_merge`]) reads nothing from its build input, `settle` and `explain` read only the spec and the settlement modes, `subset` reads only the subset pack's path and header values, and `shape`, `ink`, `enrich`, `scaffold`, `expect`, `draft` and `fragment` refuse an init whose HarfBuzz version is not the linked one. A refused input exits 1 with the reason on stderr.
//!
//! A usage mistake (no subcommand, an unknown one, an argument a subcommand does not take, or an argument that is not valid Unicode) exits 2 with the usage on stderr.

#![forbid(unsafe_code)]

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use ams_review::service::Ending;

const USAGE: &str = "usage: ams-review version\n       ams-review units --init=PATH --threads=N\n       ams-review stage {wire,pyfmt,yaml,shape,ink,settle,explain,subset,enrich,scaffold,expect,schema,draft,fragment,check,build-input,sink,containers,json-forms,sample,row-delta-read,row-delta-write,audit-layout,stream,signature-store,signature,key-only,columns,permutation,merge} --init=PATH FILE";

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
        ["units", init, threads] => match (
            init.strip_prefix("--init="),
            threads
                .strip_prefix("--threads=")
                .and_then(|threads| threads.parse::<usize>().ok()),
        ) {
            (Some(init), Some(threads)) => units(init, threads),
            _ => usage(),
        },
        [
            "stage",
            name @ ("wire" | "pyfmt" | "yaml" | "shape" | "ink" | "settle" | "explain" | "subset"
            | "enrich" | "scaffold" | "expect" | "schema" | "draft" | "fragment" | "check"
            | "build-input" | "sink" | "containers" | "json-forms" | "sample"
            | "row-delta-read" | "row-delta-write" | "audit-layout" | "stream"
            | "signature-store" | "signature" | "key-only" | "columns" | "permutation"
            | "merge"),
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

fn units(init: &str, threads: usize) -> ExitCode {
    let output = std::io::BufWriter::new(std::io::stdout());
    match ams_review::service::serve(
        PathBuf::from(init),
        threads,
        std::io::BufReader::new(std::io::stdin()),
        output,
    ) {
        Ending::Stopped => ExitCode::SUCCESS,
        Ending::Failed => ExitCode::from(1),
        Ending::Closed => {
            eprintln!("ams-review: units: the input closed without stop");
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
        "pyfmt" => {
            ams_review::pyfmt::stage_pyfmt(file, &mut stdout).map_err(|error| error.to_string())
        }
        "yaml" => {
            ams_review::pyfmt::stage_yaml(file, &mut stdout).map_err(|error| error.to_string())
        }
        "shape" => ams_review::shape::stage_shape(init, file, &mut stdout)
            .map_err(|error| error.to_string()),
        "ink" => {
            ams_review::ink::stage_ink(init, file, &mut stdout).map_err(|error| error.to_string())
        }
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
        "fragment" => ams_review::fragment::stage_fragment(init, file, &mut stdout)
            .map_err(|error| error.to_string()),
        "build-input" => ams_review::corpus::input::stage_build_input(init, &mut stdout)
            .map_err(|error| error.to_string()),
        "sink" => ams_review::corpus::sink::stage_sink(file, &mut stdout)
            .map_err(|error| error.to_string()),
        "containers" => ams_review::corpus::gzip::stage_containers(file, &mut stdout)
            .map_err(|error| error.to_string()),
        "json-forms" => ams_review::pyfmt::stage_json_forms(file, &mut stdout)
            .map_err(|error| error.to_string()),
        "sample" => ams_review::corpus::random::stage_sample(file, &mut stdout)
            .map_err(|error| error.to_string()),
        "row-delta-read" => ams_review::corpus::rowdelta::stage_row_delta_read(file, &mut stdout)
            .map_err(|error| error.to_string()),
        "row-delta-write" => {
            ams_review::corpus::rowdelta::stage_row_delta_write(init, file, &mut stdout)
                .map_err(|error| error.to_string())
        }
        "audit-layout" => ams_review::corpus::audit::stage_audit_layout(init, file, &mut stdout)
            .map_err(|error| error.to_string()),
        "stream" => ams_review::corpus::stream::stage_stream(init, file, &mut stdout)
            .map_err(|error| error.to_string()),
        "signature-store" => {
            ams_review::corpus::signatures::stage_signature_store(init, file, &mut stdout)
                .map_err(|error| error.to_string())
        }
        "signature" => ams_review::corpus::signatures::stage_signature(init, file, &mut stdout)
            .map_err(|error| error.to_string()),
        "key-only" => ams_review::corpus::signatures::stage_key_only(init, file, &mut stdout)
            .map_err(|error| error.to_string()),
        "columns" => ams_review::corpus::columns::stage_columns(file, &mut stdout)
            .map_err(|error| error.to_string()),
        "permutation" => ams_review::corpus::table::stage_permutation(file, &mut stdout)
            .map_err(|error| error.to_string()),
        "merge" => ams_review::corpus::table::stage_merge(file, &mut stdout)
            .map_err(|error| error.to_string()),
        _ => ams_review::check::stage_check(init, file, &mut stdout)
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
