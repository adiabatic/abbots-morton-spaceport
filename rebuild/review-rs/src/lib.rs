//! The `ams-review` library: the review corpus build's unit recompute, which the `ams-review` binary serves. `rebuild/CORPUS-UNITS-PORT-PLAN.md` is the design, and § Boundary there holds the formats the binary reads and writes.
//!
//! The library reads the settlement model through the kernel crate's public API (`ams_m1_kernel`) and never edits it. It shapes through the HarfBuzz `build.rs` compiles in, maps the subset pack through the C library's `mmap`, deflates fragments through the system zlib, reads its peak footprint through the C library and takes the descriptors a parent passes it, and `ffi`, which declares all five, is the only module that allows `unsafe`. [`unit`] is the worker, and [`service`] serves it over the frames of § Boundary. [`corpus`] is the build's parent, which `rebuild/CORPUS-PARENT-PORT-PLAN.md` ports beside it.

#![deny(unsafe_code)]

pub mod change_groups;
pub mod check;
pub mod classify;
pub mod config_fold;
pub mod corpus;
pub mod drafts;
pub mod enrich;
pub mod expect;
pub mod explain;
mod ffi;
pub mod fontfacts;
pub mod fragment;
pub mod ink;
pub mod pack;
pub mod pyfmt;
pub mod scaffold;
pub mod schema;
pub mod service;
pub mod settle;
pub mod shape;
pub mod subset_pack;
pub mod unit;
pub mod wire;

/// The format marker of the boundary between the build and the binary, carried by the init file whose frames and records § Boundary of `rebuild/CORPUS-UNITS-PORT-PLAN.md` describes ([`wire`]).
pub const WIRE_FORMAT: &str = wire::INIT_FORMAT;

/// What `ams-review version` prints: one `name<TAB>value` line for the wire format, one for the build input's format ([`corpus::input::INPUT_FORMAT`]), one for the spec dump format the kernel reads (`ams_m1_kernel::SPEC_FORMAT`), and one for the HarfBuzz version the engine links ([`shape::harfbuzz_version`]).
pub fn version_text() -> String {
    format!(
        "wire\t{WIRE_FORMAT}\nbuild\t{}\nspec\t{}\nharfbuzz\t{}\n",
        corpus::input::INPUT_FORMAT,
        ams_m1_kernel::SPEC_FORMAT,
        shape::harfbuzz_version()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_version_names_both_formats_the_kernels_spec_format_and_the_linked_harfbuzz() {
        assert_eq!(
            version_text(),
            "wire\tams-review-units-init/1\nbuild\tams-review-build/1\nspec\tams-m1-spec/1\nharfbuzz\t12.3.2\n"
        );
    }
}
