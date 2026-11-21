//! The review corpus build's parent, which `rebuild/CORPUS-PARENT-PORT-PLAN.md` moves from `rebuild/review/build.py` into the crate beside the units engine, with `rebuild/review/corpus_exec.py` as its Python half. [`input`] reads the build input that interface writes, [`console`] prints the build's console lines, and [`sink`] writes its whole-file outputs as `output_sink` does.

pub mod console;
pub mod input;
pub mod sink;
