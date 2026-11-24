//! The `ams-review` library: the review corpus build, which the `ams-review` binary runs. `doc/rebuild-design.md` §14.2 holds the crate's design facts.
//!
//! The library reads the settlement model through the kernel crate's public API (`ams_m1_kernel`) and never edits it. It shapes through the HarfBuzz `build.rs` compiles in, maps the subset pack through the C library's `mmap`, deflates and inflates through the system zlib, reads its peak footprint and resident set through the C library and takes the descriptors a parent passes it, and `ffi`, which declares all five, is the only module that allows `unsafe`. [`corpus`] is the build; its units phase runs [`unit`]'s workers on [`pool`]'s threads over one shared set of resources.

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
pub mod pool;
pub mod pyfmt;
pub mod scaffold;
pub mod schema;
pub mod settle;
pub mod shape;
pub mod subset_pack;
pub mod unit;
pub mod wire;

/// The format marker of the units init that `units_exec.write_init` writes and [`wire::Init`] reads.
pub const WIRE_FORMAT: &str = wire::INIT_FORMAT;

/// What `ams-review version` prints: one `name<TAB>value` line for the units init's format, one for the build input's format ([`corpus::input::INPUT_FORMAT`]), one for the spec dump format the kernel reads (`ams_m1_kernel::SPEC_FORMAT`), one for the HarfBuzz version the engine links ([`shape::harfbuzz_version`]), and one for the version of the system zlib it links, which the corpus build's containers are byte for byte Python's only under (`zlibVersion()`, Python's `zlib.ZLIB_RUNTIME_VERSION`).
pub fn version_text() -> String {
    format!(
        "wire\t{WIRE_FORMAT}\nbuild\t{}\nspec\t{}\nharfbuzz\t{}\nzlib\t{}\n",
        corpus::input::INPUT_FORMAT,
        ams_m1_kernel::SPEC_FORMAT,
        shape::harfbuzz_version(),
        ffi::zlib_version()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_version_names_both_formats_the_kernels_spec_format_the_linked_harfbuzz_and_zlib() {
        assert_eq!(
            version_text(),
            format!(
                "wire\tams-review-units-init/1\nbuild\tams-review-build/1\nspec\tams-m1-spec/1\nharfbuzz\t12.3.2\nzlib\t{}\n",
                ffi::zlib_version()
            )
        );
    }
}
