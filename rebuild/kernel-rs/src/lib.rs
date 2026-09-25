//! The `ams-m1-kernel` library: every module the binary is built from. `main.rs` describes what the binary does and which Python modules define its boundaries.
//!
//! `parse::parse_spec` reads an `ams-m1-spec/1` dump into the interned model, and `emit` writes the model back out in canonical form. Outside tests, `parse` holds the crate's only `serde_json::Value`, and it is dropped inside `parse_spec`, which returns the model. `emit` therefore has only the model to write from, so a byte-identical `spec-echo` shows the packing lost nothing.

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
pub mod hash;
pub mod index;
pub mod liveness;
pub mod memo;
pub mod model;
pub mod options;
pub mod parse;
pub mod replay;
pub mod rulefold;
pub(crate) mod sha256;
pub mod shipped_order;
pub mod specificity;
pub mod stream;
pub mod types;

/// The value of every dump's `format` key. `parse::parse_spec` rejects a dump with any other value, as `kernel_io.spec_of` does.
pub const SPEC_FORMAT: &str = "ams-m1-spec/1";
