//! The kernel's ingest half: read an `ams-m1-spec/1` dump into an interned, integer-packed model, and write that model back out in the canonical spelling. `main.rs` carries the crate's orientation — what the binary is for, and which Python modules still bind its boundaries.
//!
//! The layering is deliberate and is what the round-trip proves. `parse` owns the only `serde_json::Value` in the crate and it dies inside `parse::parse_spec`, whose return type is the model; `emit` therefore has nothing to echo from but the model itself, so a byte-identical echo is evidence the packing lost nothing rather than evidence the parse tree was retained.

#![forbid(unsafe_code)]

pub mod artifacts;
pub mod cases;
pub mod census;
pub mod certificate;
pub mod emit;
pub mod engine;
pub mod error;
pub mod fanout;
pub mod fiber;
pub mod fixpoint;
pub mod fold;
pub mod guard;
pub mod index;
pub mod liveness;
pub mod model;
pub mod options;
pub mod parse;
pub mod replay;
pub mod rulefold;
pub(crate) mod sha256;
pub mod specificity;
pub mod stream;
pub mod types;

/// The marker every dump's `format` key carries. A dump naming anything else is refused rather than guessed at, exactly as `kernel_io.spec_of` refuses it.
pub const SPEC_FORMAT: &str = "ams-m1-spec/1";
