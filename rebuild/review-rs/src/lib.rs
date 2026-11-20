//! The `ams-review` library: the review corpus build's unit recompute, which the `ams-review` binary serves. `rebuild/CORPUS-UNITS-PORT-PLAN.md` is the design, and § Boundary there holds the formats the binary reads and writes.
//!
//! The library reads the settlement model through the kernel crate's public API (`ams_m1_kernel`) and never edits it.

#![deny(unsafe_code)]

/// The format marker of the boundary between the build and the binary, carried by the init file whose frames and records § Boundary of `rebuild/CORPUS-UNITS-PORT-PLAN.md` describes.
pub const WIRE_FORMAT: &str = "ams-review-units-init/1";

/// What `ams-review version` prints: one `name<TAB>value` line for the wire format and one for the spec dump format the kernel reads (`ams_m1_kernel::SPEC_FORMAT`).
pub fn version_text() -> String {
    format!(
        "wire\t{WIRE_FORMAT}\nspec\t{}\n",
        ams_m1_kernel::SPEC_FORMAT
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_version_names_the_wire_format_and_the_kernels_spec_format() {
        assert_eq!(
            version_text(),
            "wire\tams-review-units-init/1\nspec\tams-m1-spec/1\n"
        );
    }
}
