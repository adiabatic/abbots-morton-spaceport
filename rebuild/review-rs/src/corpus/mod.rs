//! The review corpus build's parent, which `rebuild/CORPUS-PARENT-PORT-PLAN.md` moves from `rebuild/review/build.py` into the crate beside the units engine, with `rebuild/review/corpus_exec.py` as its Python half. [`input`] reads the build input that interface writes, [`console`] prints the build's console lines, [`sink`] writes its whole-file outputs as `output_sink` does, [`gzip`] writes and reads its zlib and gzip containers as Python's `zlib` and `gzip` do, [`random`] draws the verification sample as CPython's `random.Random` does, [`rowdelta`] reads and writes the segment format's heads, parts and segments as `row_delta` does, and [`audit`] finds the held audit's sections as `audit.audit_layout` does.

pub mod audit;
pub mod console;
pub mod gzip;
pub mod input;
pub mod random;
pub mod rowdelta;
pub mod sink;
