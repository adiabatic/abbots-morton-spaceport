//! The review corpus build's parent, which `rebuild/CORPUS-PARENT-PORT-PLAN.md` moves from `rebuild/review/build.py` into the crate beside the units engine, with `rebuild/review/corpus_exec.py` as its Python half. [`input`] reads the build input that interface writes.

pub mod input;
