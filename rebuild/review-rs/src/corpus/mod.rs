//! The review corpus build's parent, which `rebuild/CORPUS-PARENT-PORT-PLAN.md` moves from `rebuild/review/build.py` into the crate beside the units engine, with `rebuild/review/corpus_exec.py` as its Python half. [`input`] reads the build input that interface writes, [`console`] prints the build's console lines, [`sink`] writes its whole-file outputs as `output_sink` does, [`gzip`] writes and reads its zlib and gzip containers as Python's `zlib` and `gzip` do, [`random`] draws the verification sample as CPython's `random.Random` does, [`rowdelta`] reads and writes the segment format's heads, parts and segments as `row_delta` does, [`audit`] finds the held audit's sections as `audit.audit_layout` does, [`stream`] walks them a window at a time as `audit_stream.lockstep` does, [`signatures`] loads and writes the ink-signature store as `unit_cache.SignatureArrays` does and finds and shapes its misses on threads as the build's key-only read and shaping do, [`columns`] holds the string table, the pools and the indexes the build's columns are built on as `rebuild/review/columns.py` does, and [`table`] orders the unit table's rows by `audit.triage_key` as `audit`'s counting sorts do, merges a window's ink-duplicate units as `audit._merge_members` does, and holds the unit table the build's stream loads as `audit.stream_workload` loads it; [`premerge`] holds the pre-merge snapshot the review facts are derived from, rebased onto the merge, as `facts.PremergeSnapshot` holds it.

pub mod audit;
pub mod columns;
pub mod console;
pub mod gzip;
pub mod input;
pub mod premerge;
pub mod random;
pub mod rowdelta;
pub mod signatures;
pub mod sink;
pub mod stream;
pub mod table;
