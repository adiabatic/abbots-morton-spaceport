# Corpus units port plan

This plan moves the review corpus build's unit recompute into Rust: what one units-pool worker does per unit, `_corpus_worker` in `rebuild/review/build.py` and everything it calls. The parent stays in Python, Python stays the engine of record, and the Rust engine is opt-in until the owner flips the default.

The measurements behind it are in `var/keep/rust-port/profile-corpus-units.md`, and the HarfBuzz spike's notes, results, harness and Rust source are in `var/keep/rust-port/spike-harfbuzz/`; both are kept on the M4 Pro Mac mini and not checked in. The profile's split table says where worker wall time goes, its port-surface table lists the Python this plan mirrors, and its byte-identity section names the targets.

## Decided

These are settled and are not reopened here.

1. Rust only: no C++ of our own and no PyO3. The Rust side runs as a subprocess with documented formats, and one Python interface module owns them, in the pattern of `rebuild/pipeline/kernel_io.py` and `rebuild/pipeline/kernel_exec.py`. Nothing pickled crosses the boundary.
2. Shaping uses the real HarfBuzz library through FFI, and a test on our fonts and texts proves its identity with uharfbuzz.
3. Settlement runs in process through the kernel crate's own engine, so the Rust path starts no `settle-cases` process.
4. One Rust process with threads replaces the pool of Python workers and shares the fonts, the spec and the mapped subset pack.
5. Python stays the engine of record. The Rust path is opt-in behind a switch, as commit f8e4d7bd offered the grouped sweep. No Python is deleted, and no existing test changes to fit the port.
6. The acceptance gate is a dual run over every unit of the full corpus: every projection field equal, and every fragment equal after inflating. The deflated bytes need not match.
7. The parent (load, plan, write, manifest, cache) stays in Python. It is the next candidate, so the boundary keeps that move open.

## Layout

**A sibling crate, `rebuild/review-rs/`, not a Cargo workspace.** The crate is `ams-review`: a library, `ams_review`, that holds the worker, and a thin binary, `ams-review`, whose `units` subcommand is the service the build talks to. It depends on the kernel as a path dependency (`ams-m1-kernel = { path = "../kernel-rs" }`) and uses only the kernel's public library API, and on `serde_json` through the kernel's own entry, features included, so the review lock adds no package for it. It has its own `Cargo.toml`, `Cargo.lock` and `target/`.

A workspace is ruled out for three reasons:

- The table build's code closure is `run_m1.table_build_code_paths`: the kernel's `Cargo.toml`, `Cargo.lock` and every file under its `src/`. A workspace moves the lock and `target/` to the workspace root, which changes `kernel_exec.BINARY`, `MANIFEST` and `LOCK_PATH`, `fingerprint.pipeline_code_paths`, `unit_cache.corpus_code_paths` and `run_m1.table_build_code_paths`, and `rebuild/test_build_code_closure.py::test_the_table_build_code_is_the_whole_crate_and_the_roster_on_disk` asserts the kernel's own lock, so an existing test would change (decision 5).
- A workspace has one lock. Every review-side dependency edit (the HarfBuzz build, zlib) would rewrite the lock the table build's closure hashes raw, and force a cold table build.
- The kernel library is `#![forbid(unsafe_code)]` and FFI needs `unsafe`, so the HarfBuzz code cannot live in the kernel crate under either layout.

The sibling layout costs this:

- The kernel library compiles a second time, into `rebuild/review-rs/target/`, and in that build the review crate's lock, not the kernel's, picks the versions of the kernel's dependencies (`serde_json`, `sha2`). The crate-skeleton task seeds the review lock from the kernel's and adds a test that every package both locks name has one version. The review crate's release profile copies the kernel's (`lto = true`, `codegen-units = 1`), because the root package's profile compiles its path dependencies.
- A kernel edit moves both the table build's closure and the review engine, as it should.

**No edit to `rebuild/kernel-rs/`.** Everything the worker needs is public already: `parse::parse_spec`; `index::SpecIndex` (`new`, `sym_of`, `resolve`, `letter`, `spec`); `engine::{Engine, EngineModes, Slots}` with `Engine::with_modes`, `transition_trace` and `release_memos`; `types::{LeftContext, Settled, CellId, TransitionTrace, TraceRanking, cell_label, adjustment_text, provenance_pointer}`; `token::{EDGE, SPACE, ZWNJ, NAMER_DOT, UNKNOWN}`; `guard::sweep`; `emit::json_string`. Two private pieces, `engine_for` in the kernel's `main.rs` and `Formation` in its `replay.rs`, are a few lines each and are restated in the review crate. A task that finds it needs more stops and records what it needs instead of editing the kernel: a visibility edit moves the table build's closure and costs a cold table build, so it lands alone, by the owner's decision.

**Only `ams-review` links HarfBuzz.** `ams-m1-kernel` never does. The crate root is `#![deny(unsafe_code)]`, and the FFI module alone allows it.

**What each closure sees.** Nothing the table build hashes changes. The unit store's stamp sees the crate only under the Rust engine (§ The switch). The interface module, `rebuild/review/units_exec.py`, is a review module `build.py` imports, so `fingerprint.review_code_paths` and `unit_cache.corpus_code_paths` hash it like any other, and each edit to it costs the next cycle a cold units phase. It may import only modules under `rebuild/review/`, `rebuild/pipeline/` and `rebuild/validation/`, and the members of `WIDTH_AND_TELEMETRY_MODULES` (`rebuild/test_review_code_closure.py`). The tools, `rebuild/tools/units_stage_fixtures.py` and `rebuild/tools/units_dual_run.py`, are in no closure.

The crate's modules, which the tasks add in order:

| Module                                     | Mirrors                                                                                                                                                                           |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `wire.rs`, `pyfmt/mod.rs`                  | the frames and records of § Boundary, with `units_exec.py` as the Python half; `PyValue`, the crate's model of a Python value, and the stage dumps' typed encoding                |
| `pyfmt/{repr,json,yaml,unicode_tables}.rs` | CPython's `repr` and `json.dumps`, PyYAML's flow `safe_dump`, and the `str` predicates they rest on                                                                               |
| `fontfacts.rs`                             | the font facts `units_exec.write_font_facts` writes through `ink.OutlineCache` and `ink.OutlineIntern`, the sets `classify.JunctionClassifier` reads, `shaping.Shaper.glyph_name` |
| `ffi.rs`, `shape.rs`                       | `shaping.Shaper.shape`, `ink._MemoizedShaper`                                                                                                                                     |
| `ink.rs`, `config_fold.rs`                 | `ink.InkComparator` up to its picture path, `ink._picture_diff`, `ink.JuniorOracle.approves`, `ink.delta_digest`, `feature_reach.ConfigFold.effective`, `build._ink_readings`     |
| `settle.rs`, `explain.rs`                  | `settle.tokens_from_codepoints`, `settle.form_ligatures`, `kernel_exec.settle_sequences`, `explain.explain_many`, `explain.ExplainReport.render`                                  |
| `subset_pack.rs`                           | `subset_pack.SubsetPack`                                                                                                                                                          |
| `enrich.rs`                                | `enrich.Enricher.enrich` and its helpers, `enrich.primary_unit_projection`                                                                                                        |
| `change_groups.rs`, `scaffold.rs`          | `change_groups.py`; `build.unit_scaffold`, the config badge, `build._cluster_id_from_repr`                                                                                        |
| `expect.rs`, `classify.rs`                 | `test/test_shaping.py`'s `parse_expect` and helpers, `pins._check_interpretation`, `shaping.row_for`, `classify.JunctionClassifier.classify`                                      |
| `schema.rs`                                | `spec_load._SchemaValidator` over `rebuild/schema/rune.schema.json`                                                                                                               |
| `drafts.rs`                                | `drafts.Drafter`                                                                                                                                                                  |
| `fragment.rs`, `pack.rs`                   | `build.unit_to_json`, `build.patch_fragment`, `build.check_stamp`, `build._fragment_bytes`, `build._FragmentPacker`, the `unit_cache` key and id helpers                          |
| `check.rs`                                 | `build.check_unit` at `DRAFTED`                                                                                                                                                   |
| `unit.rs`                                  | `build._phase1_unit`, `build._recompute_fragment`, `build._UnitProjection`                                                                                                        |
| `service.rs`, `main.rs`                    | `build._corpus_worker`'s message loop                                                                                                                                             |

## Boundary

### The process

`ams-review units --init=<path> --threads=<n>` reads request frames on its standard input and writes reply frames on its standard output. Standard error stays empty on a clean run, the rule `kernel_exec` holds the kernel to. It exits 0 after `stop`, 1 after an `error` frame, and 1 when its input closes without `stop`, so a parent that dies takes it down. One reader thread drains standard input into a queue and one writer thread serializes replies, so the parent never blocks writing a batch while the engine blocks writing a reply.

A frame is one line of compact JSON ending in a newline, an object whose `op` names it. A reply that carries fragments also has `payload`, a byte count, and exactly that many raw bytes follow the newline. Every number is an integer; no float crosses.

### The init file

`units_exec.write_init` writes the init file, `ams-review-units-init/1`, into a `TemporaryDirectory` it holds until the engine stops, beside the spec dump, the font facts and the preset dictionary. The build writes it from the values the Python worker's `init` dict carries (`_RecomputeRunner.__init__`).

| Field                 | Holds                                                                                                         | Python source                                                                                                                                    |
| --------------------- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `format`              | `ams-review-units-init/1`                                                                                     | `units_exec.INIT_FORMAT`                                                                                                                         |
| `spec`                | the path of an `ams-m1-spec/1` dump                                                                           | `kernel_io.write_spec` over `enrich.load_spec(spec_root)`                                                                                        |
| `settlement`          | `simulated_prospect` and `follower_prefer_slots`                                                              | `kernel_exec.SIMULATED_PROSPECT_DEFAULT` and `FOLLOWER_PREFER_SLOTS_DEFAULT`, read at call time                                                  |
| `harfbuzz`            | the HarfBuzz version the Python side shapes with                                                              | `uharfbuzz.version_string()`                                                                                                                     |
| `fonts`               | `before`, `after` and `junior`, each a path                                                                   | the runner's three fonts                                                                                                                         |
| `font_facts`          | the path of the font facts file                                                                               | `units_exec.write_font_facts`                                                                                                                    |
| `junior_tracking`     | the Junior letter tracking                                                                                    | `ink.JuniorOracle(...).tracking`; `write_init` constructs the oracle, so a failed tracking premise raises in the parent as it raises in a worker |
| `aliases`             | `[old glyph name, cell or string]` pairs in file order                                                        | `labels.load_alias_map` over `rebuild/m1-aliases.yaml`                                                                                           |
| `subset_pack`         | the path, and the format, packer digest, byte order and per-configuration table digests its header must carry | the runner's `subset_pack`; `subset_pack.PACKER_DIGEST`, `subset_pack.table_digests`                                                             |
| `fold`                | the configurations in order, and each one's trigger code points                                               | the runner's `ConfigFold` (`configs`, `triggers`)                                                                                                |
| `identical_classes`   | the ledger's `ink_identical` ids, sorted                                                                      | the runner's `identical_classes`                                                                                                                 |
| `corpus_index`        | `[text, config token, source, attribute]` in the mapping's order                                              | the runner's `corpus_index`                                                                                                                      |
| `rune_schema`         | the path                                                                                                      | `rebuild/schema/rune.schema.json` under `repo_root`                                                                                              |
| `glyph_names_by_char` | `[code point, glyph name]` pairs                                                                              | `test_shaping._CHAR_TO_GLYPH`, through `drafts._import_test_shaping`                                                                             |
| `dictionary`          | the path of the preset dictionary's bytes                                                                     | the bytes `build_m1` read once from `FRAGMENT_DICTIONARY`                                                                                        |
| `vocabulary`          | every module-level constant the ported code reads                                                             | `units_exec.VOCABULARY`, the authority; the engine refuses an init that lacks one                                                                |

Rust parses no YAML: every YAML input reaches it as JSON Python wrote or, for the spec, as the kernel's dump.

Nothing else crosses. The change groups' prose stays out, because only the manifest reads it (`change_groups.load_change_group_prose`); the worker's `init` dict has no such key. So do the ledger's prose, the site pages (their index crosses), and the spec's YAML.

The font facts file, `ams-review-font-facts/1`, puts the three fonts in one shape space: a shape key derives from the outline's content, so keys compare across fonts and across the Python worker's several interns. For each font it holds the glyph order, one row per glyph id: `[glyph name, shape key, origin x, origin y]` from `OutlineCache.shape_key`, with the key as lowercase hex and empty for a glyph with no outline (`EMPTY_OUTLINE_KEY`). One shape table, keyed by the same hex, holds `[drawn, cells]` from `OutlineIntern.draws` and `OutlineIntern.cells`, the cells as sorted `[column, row]` pairs or null for a shape with no grid picture. The after font's section also holds `JunctionClassifier`'s exit and entry sets by height, built by its constructor. The writer refuses an origin that is not an integer. Python computes every value through the ink module's own classes, so the facts equal what a Python worker reads by construction, and the engine reads no outline.

### Batches in

A batch request is `{"op": "batch", "batch": n, "units": [...]}`, one record per `audit.Unit` that `UnitTable.unit` materialized for the handout.

| Field                        | `audit.Unit` field           | Note                                                                   |
| ---------------------------- | ---------------------------- | ---------------------------------------------------------------------- |
| `ordinal`                    | `ordinal`                    |                                                                        |
| `codepoints`                 | `codepoints`                 | colon-joined hex, as `audit.format_codepoints` writes it               |
| `new`                        | `new`                        | read only for the mismatch line                                        |
| `class`                      | `class_id`                   |                                                                        |
| `configs`                    | `configs`                    |                                                                        |
| `kinds`                      | `kinds`                      |                                                                        |
| `group`                      | `group`                      |                                                                        |
| `exemplar`                   | `exemplar`                   | phase 1 clears it on a re-routed unit                                  |
| `unit_id`                    | `unit_id`                    | the store's id or empty; phase 1 replaces it                           |
| `input_key`                  | `input_key`                  |                                                                        |
| `render_groups`              | `render_groups`              |                                                                        |
| `no_verdict`                 | `no_verdict`                 |                                                                        |
| `config_classes`             | `config_classes`             | `[config, class]` pairs in the mapping's order                         |
| `duplicate_group`, `cluster` | `duplicate_group`, `cluster` | placeholders the drafted fragment carries until the write patches them |
| `ledger_class`               | `ledger_class`               | phase 1 replaces it                                                    |

The machine flags and `ink_deltas` do not cross: phase 1 assigns the flags before anything reads them, and every materialized unit carries `audit.NO_DELTAS`. Phase 1 reads no other field.

### Replies out

A batch reply is `{"op": "batch", "batch": n, "projections": [...], "contract_errors": [...], "payload": bytes}`, followed by the batch's deflated fragments end to end in projection order. `contract_errors` is `check_unit`'s errors at `DRAFTED` in unit order, capped at `CONTRACT_ERRORS_SHOWN` (`_keep_contract_errors`). Each projection uses the unit store's record shapes (`unit_cache.CachedUnit.to_record`):

| Field                                                     | `_UnitProjection` field                                                    | Shape                                              |
| --------------------------------------------------------- | -------------------------------------------------------------------------- | -------------------------------------------------- |
| `ordinal`                                                 | `ordinal`                                                                  |                                                    |
| `codepoints`                                              | `primary_unit_projection.codepoint_values`                                 | colon-joined hex                                   |
| `id`                                                      | `unit_id`, and the primary-unit projection's                               |                                                    |
| `input_key`                                               | `input_key`                                                                |                                                    |
| `content_key`                                             | `content_key`                                                              | lowercase sha256 hex                               |
| `ink_identical`, `picture_identical`, `junior_equivalent` | the three flags, and the primary-unit projection's two                     |                                                    |
| `ink_deltas`                                              | `ink_deltas`                                                               | an object in the unit's config order               |
| `diffs_digest`, `cluster`, `change_group`, `ledger_class` | the same                                                                   |                                                    |
| `pair_codepoints`                                         | `pair_codepoints`                                                          | `[start, end]` or null                             |
| `proj`                                                    | `primary_unit_projection` without its id, flags, window and junction pairs | `UnitStore.primary_unit_projection_record`'s shape |
| `junctions`                                               | `junction_rects`, and the projection's `junction_pairs`                    | `build._junction_records`' shape                   |
| `mismatches`                                              | `mismatches`                                                               |                                                    |
| `raw_length`                                              | `raw_length`                                                               | the length of the fragment's shard bytes           |
| `packed_length`                                           | the length of `packed`                                                     | the fragment's span in the payload                 |

A deflated fragment is a zlib stream at level 1 against the preset dictionary, so its header carries the dictionary's Adler-32 and `_FragmentArena.read` inflates it as it inflates a Python worker's.

### Verify, stop and errors

- `{"op": "verify", "units": [{"unit": ..., "injection": {"duplicate_group": ..., "cluster": ..., "class": ..., "primary_units": [[id or null, suppressed], ...]}}]}` recomputes and patches each sampled cached unit as `_recompute_fragment` does. The reply is `{"op": "verify", "keys": [[unit id, content key, ink deltas], ...]}`, each keyed by the id the recomputation stamped (§ Byte-identity hazards, verify).
- `{"op": "stop"}` gets `{"op": "stopped", "peak_footprint": bytes}`, the process's peak footprint as `peak_rss.peak_footprint_self_bytes` reads it, and the process exits 0.
- A failure in any thread sends `{"op": "error", "message": ...}`, naming the unit, and the process exits 1. The message need not match a Python traceback; the same units must fail.
- A unit whose ink needs the outline-level delta (`InkComparator._piece_diff`) fails with an error frame that names it and says so. Building such a unit is the Python engine's job (§ Byte-identity hazards, the outline-level delta).

### Where Python consumes the replies

`_RecomputeRunner` keeps its interface. In the pooled branch of `__init__`, under the Rust engine, it starts one `units_exec.UnitsProcess` at `nworkers` threads instead of `nworkers` spawn processes. `_drive_phase1` hands out the same handouts (`_configuration_order`, `_handout_width`), at most one per thread in flight, and loads each reply as it arrives: `units_exec.decode_batch` turns the projections into `RustProjection`s, which satisfy `unit_store.RecomputedProjection` and carry `packed` and `raw_length`, and the loop calls `_load_recomputed(store, table, self.arena, projection)` and `_keep_contract_errors(self.contract_errors, errors)` exactly as for a worker's `batch` reply. `_load_recomputed`'s annotation widens to a protocol both projection types satisfy. `verify` sends one `verify` frame and returns the mapping the Python pool returns. `stop_workers` sends `stop` and writes a pool record under its own unit, `corpus-units-rs`, with the process's peak footprint and the thread count. An `error` frame raises with its message, as a worker's `error` reply does, and so does end of output before `stopped`; like the Python pool, the parent waits on the engine with no timeout.

### The switch

`units_exec.engine()` reads `AMS_CORPUS_UNITS_ENGINE` at call time, `python` or `rust` with `python` the default, and refuses any other value, as `kernel_exec` reads its semantics defaults. It is the only switch: a cycle opts in through its environment (`AMS_CORPUS_UNITS_ENGINE=rust make review-cycle`), and the `[t] review.build units` line names the engine. Only the pooled units phase and the verification sample change. The serial path (`--jobs 1`), which the byte-identity tests compare pooled builds against, and `--mode table-diff` stay in Python.

Under the Rust engine the unit store's stamp gains one line, `units_engine`, a digest through `fingerprint.code_file_digest` of every file under `rebuild/review-rs/` outside `target/`, so `build.rs` and the HarfBuzz pin it holds count with the sources. `build.py` passes it through a new keyword argument of `unit_cache.environment_stamp` that adds nothing by default, so with the switch off every stamp keeps its bytes (`rebuild/test_alphabet_switch.py` pins the unit store's), a crate edit invalidates only a store the Rust engine wrote, and a store either engine wrote is a miss to the other. A new test checks that every kernel module the review crate names (`ams_m1_kernel::<module>`) is outside `unit_cache.KERNEL_NON_CORPUS_MODULES`, so the kernel code the engine runs is hashed into `corpus_code` already.

### Toward the parent's port

- The worker is a library API (`ams_review::unit::Worker::new`, `run_batch`, `verify`), and the service is a thin framing layer over it, so a Rust parent links the library and drops the frames.
- The replies use the unit store's record shapes, so a Rust parent writes store lines from them with no translation.
- Every init input is a file or plain JSON with one documented producer in `units_exec`, so the producers can move to Rust one at a time. The font facts are a format, not a fontTools object.
- The crate is named for the review build, so the parent's port lands in it as another subcommand.

## HarfBuzz and outlines

The HarfBuzz spike settles this section. The `harfbuzz-shaper` task starts from its Rust source, `var/keep/rust-port/spike-harfbuzz/hbspike/`.

**Binding.** A hand-written FFI: `src/ffi.rs` declares the HarfBuzz functions and structs the engine calls, written against the 12.3.2 headers, and holds the safe types that call them (`Font`, `Feature`, `Buffer`), which `src/shape.rs` builds the shaper on; `ffi.rs` is the only module that allows `unsafe`. `build.rs` compiles HarfBuzz's amalgamated `src/harfbuzz.cc` as one C++11 translation unit with `HAVE_PTHREAD` and no platform backend, through the `cc` crate pinned at `=1.10.0` as a build dependency. The build needs cargo and a C++ compiler (on macOS, the Xcode command-line tools' `clang++`), and no meson, cmake or pkg-config. The published crates do not fit: `harfbuzz-sys` bundles another HarfBuzz version, has no draw-API bindings and turns on CoreText, FreeType and DirectWrite by default; `harfbuzz_rs` bundles another version inside a larger wrapper; `harfrust` is a reimplementation, which decision 2 rules out; and none builds the version uharfbuzz links.

**Version.** HarfBuzz 12.3.2, the version the locked uharfbuzz links. The init carries `uharfbuzz.version_string()`, the engine refuses to start when it differs from its own `hb_version_string()`, and `ams-review version` prints it. A `uv.lock` bump of uharfbuzz therefore stops the Rust engine until the pin in `build.rs` follows it and the identity test passes again.

**Source.** When `rebuild/review-rs/target/vendor/` lacks it, `build.rs` fetches the 12.3.2 release tarball from HarfBuzz's GitHub releases with `curl`, checks its sha256 against `6f6db164359a2da5a84ef826615b448b33e6306067ad829d85d5b0bf936f1bb8` (the tarball the spike built) with the `sha2` crate the kernel already locks, refuses on a difference, and unpacks its `src/` there with `tar`, leaving out HarfBuzz's own Python scripts, since pyright checks every `.py` file under `rebuild/`, ignored or not. A fresh checkout's first build needs the network, as its first `cargo build` already does for crates.io.

**Shaping.**

- One `hb_font_t` per font, made from the file's bytes at the default scale, in font units, as uharfbuzz's `hb.Font(hb.Face(blob))` is, and shared read-only by every thread; one `hb_buffer_t` per thread. The spike's threaded runs give the same output at every width.
- Each call clears the buffer, adds the text with `hb_buffer_add_utf32`, calls `hb_buffer_guess_segment_properties`, and shapes with one global feature per tag, value 1 for each tag the configuration turns on and 0 for `kern`, built as uharfbuzz builds a feature dict (`hb_feature_from_string`, then the value set). A cluster is a code point index on both sides: uharfbuzz adds BMP text as UTF-16, which gives the same clusters as UTF-32 for any valid text. After the guess both sides read language `c`, no script and left to right.
- Per glyph the engine reads the glyph id, the cluster, both offsets and both advances, and holds the two invariants `Shaper.shape` asserts (monotonic clusters, zero `y_advance`). Names come from the facts' glyph order, never from HarfBuzz, which truncates them (`Shaper.glyph_name`).
- uharfbuzz is built with CoreText and the engine's HarfBuzz is not. The OpenType shaper runs first in both, so the output is the same.

**Identity.** The spike shapes through both bindings every call the real worker makes on the profile slice, every slice text on Junior with only `kern` off, and every slice text on Senior and M1 with no features, and compares the glyph id, name, cluster, offsets and advances of every glyph: they agree everywhere, while a control with `kern` left on disagrees, so the comparison catches a real difference. `rebuild/test_units_exec.py::test_harfbuzz_shapes_like_uharfbuzz_on_the_mini_texts` keeps the check in the suite, where it also guards the hand-written declarations, whose ABI no compiler checks. It shapes every mini text through both bindings under each acceptance configuration's kern-neutral features, `ss10` included, on the site Senior font and the mini bundle's `M1.otf`, and with only `kern` off on the site Junior font: every call the worker makes is kern-neutral.

**Outlines.** The engine takes its outline data from the font facts (§ The init file) and never draws a glyph. HarfBuzz's draw API is not a substitute: it closes each contour with an explicit `lineTo` back to its start, which fontTools leaves implicit, so its operator streams, and the shape keys hashed from them, differ from fontTools' for every inked glyph, although the spike finds its cells equal.

**Speed.** The binding saves little per shape, because HarfBuzz's own work on M1 dominates (the spike's `bench-*.txt`). The engine's gain comes from threads, the shape memo and leaving the interpreter. Per-thread efficiency falls as the threads pass the performance cores, so the engine's width comes from `memory-constant`'s measurement and `corpus_job_budget`, as `doc/parallelism.md` requires.

## Byte-identity hazards

Each row names the Python site and how the Rust side meets it. `pyfmt::PyValue` is the crate's model of a Python value (int, str, None, bool, tuple, list, dict, bytes, set, and a dataclass or named tuple as a record), with no float variant.

| Hazard                                 | Python site                                                                                                                                                                                                                                                                                                                                                       | Rust approach                                                                                                                                                                                                                                                                                                                                                                                                            |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `repr()` of nested tuples under a hash | `ink.delta_digest`; `diffs_repr` in `build._phase1_unit`, which gives `diffs_digest`; `build._cluster_id_from_repr`                                                                                                                                                                                                                                               | `pyfmt::repr` over `PyValue` reproduces CPython: `(a,)` for one element, `()` for none, a comma and a space between items, decimal ints with `-`, and str quoting (single quotes unless the text holds `'` and no `"`) and escapes (`\\`, the quote, `\t`, `\n`, `\r`, then `\xNN`, `\uNNNN` or `\UNNNNNNNN` for a character `str.isprintable` rejects, which includes the private-use letters). The bytes go to `sha1`  |
| `repr()` inside strings that ship      | the two mismatch lines in `Enricher.enrich` (an f-string's `{tuple}` is `repr`); `check_unit`'s `!r` messages; `parse_expect`'s and `_token_char_spans`' errors                                                                                                                                                                                                   | the same writer, with dict and list support for `_token_char_spans`' `{tok!r}`                                                                                                                                                                                                                                                                                                                                           |
| `None` in an f-string                  | `enrich.cell_token` writes `None` for an absent height                                                                                                                                                                                                                                                                                                            | write `None` literally; never Rust's `Option` formatting                                                                                                                                                                                                                                                                                                                                                                 |
| sort-keys JSON for the content key     | `unit_cache.carry_projection` (`json.dumps(sort_keys=True)`), which names every unit id                                                                                                                                                                                                                                                                           | `pyfmt::json` with a space after each `,` and `:`, `ensure_ascii` escapes (lowercase `\uXXXX`, surrogate pairs beyond the BMP, `\"`, `\\`, `\n`, `\r`, `\t`, `\b`, `\f`, other control characters as `\u00XX`), keys sorted at every level by code point, which UTF-8 byte order equals, and no float                                                                                                                    |
| indent-1 fragment bytes                | `build._fragment_bytes`: `json.dumps([fragment], indent=1, ensure_ascii=True)[1:-2]`                                                                                                                                                                                                                                                                              | `pyfmt::json`'s indent mode: `,` then a newline and one space per depth, a space after each `:`, `[]` and `{}` for empty containers, insertion order, and the `[1:-2]` framing, so the bytes start with a newline and one space                                                                                                                                                                                          |
| fragment key order                     | the literal order of `build.unit_to_json` and `build.unit_scaffold`, the `to_json` methods in `drafts.py`, the dict literals in `enrich.py` (`_highlight`, the boundary marks); `patch_fragment` assigns in place                                                                                                                                                 | build an ordered map in each function's literal order; a slim fragment deletes `SLIM_OMITTED_KEYS` in place                                                                                                                                                                                                                                                                                                              |
| PyYAML flow output                     | `drafts.Drafter.draft_policy`: `yaml.safe_dump(record, default_flow_style=True, width=10**6, allow_unicode=True, sort_keys=False).strip()`                                                                                                                                                                                                                        | `pyfmt::yaml` writes `{k: v, ...}` and `[a, b]` in insertion order; a string is plain when PyYAML's scalar analysis allows plain in flow context and the implicit resolver would read it back as a string (not bool, int, float, null, timestamp, merge or value), else single-quoted with `''` when allowed, else double-quoted with PyYAML's escapes; `allow_unicode` keeps printable non-ASCII; the width never folds |
| the rune-schema check                  | `Drafter.__init__`'s three checkers from `spec_load._load_schema`, `_SchemaValidator`                                                                                                                                                                                                                                                                             | a validator over the closed vocabulary `_SchemaValidator._KEYWORDS` that reads the schema file at init, refuses a keyword outside the vocabulary, resolves root-local `$ref`s, and matches `pattern` with `re.search` semantics. Only the verdict reaches output (`schema_valid` is always true; a failure raises `DraftError`), so the verdict is the target and the problem text is not                                |
| floats                                 | none reaches an output: HarfBuzz positions are integers in font units, and the facts carry integer origins and cells                                                                                                                                                                                                                                              | `PyValue` and the wire have no float; the facts writer refuses an origin that is not an integer                                                                                                                                                                                                                                                                                                                          |
| dict and set order                     | `dict.fromkeys(effective)` in `_ink_readings`; the `ink_deltas` comprehension (the unit's config order); `config_classes` (the table's mapping order); `_config_class_note`; `_collect_provenance`; `features_dict`; `_baseline_cell_pin`; sets appear only for membership or under `sorted` (`_diff_codepoints`, `_config_badge`)                                | ordered vectors on every output path, hash containers only for lookups; the unit record carries `config_classes` as ordered pairs                                                                                                                                                                                                                                                                                        |
| itertools order                        | `build._candidate_constraint_sets` (nested `combinations`, first match wins); `test_shaping._expand_maybe_ligatures` (`product([True, False], ...)`)                                                                                                                                                                                                              | generate in itertools' index order                                                                                                                                                                                                                                                                                                                                                                                       |
| code points beyond the BMP             | texts built with `chr`; uharfbuzz clusters; `subset_pack.pack_key` refuses above U+FFFF and `SubsetPack.row` gives None; `json.dumps` surrogate pairs; `repr`'s `\U`; `{value:04X}` in `text_entities` and `notation`; `len(str)`                                                                                                                                 | code points as `u32` or `char`, never byte offsets; HarfBuzz takes UTF-32; `pack_key` refuses as Python's does; `{:04X}`                                                                                                                                                                                                                                                                                                 |
| Python's `str` predicates              | `str.isprintable` in `repr`; `str.isspace` behind `strip`, `lstrip` and `\s` in `parse_expect`; `str.isdigit` in `subset_pack.is_junction_token` and `change_groups._height`; `int(part, 16)` in `subset_pack.pack_key`, which strips non-ASCII whitespace and reads any script's decimal digits (`str.isdecimal`)                                                | tables generated from the pinned CPython by `units_stage_fixtures unicode-tables` into `pyfmt/unicode_tables.rs`; `--check` keeps them current. Rust's `char::is_whitespace` differs from `str.isspace` on several control characters                                                                                                                                                                                    |
| sort stability and keys                | `settle._ligature_order` (stable by length over the spec's rune order); `_config_class_note` (`sorted(key=_config_index)`, ties keep order); the `min` key in `enrich` that picks a boundary mark; `ink_pieces`, `junior_pieces` and `enrich._segment_pieces` (tuples led by a shape key); `_picture_diff`; `diff_positions`; `_secondary_pairs`; `_gate_clauses` | stable sorts only, each key function ported literally; a shape key sorts as the facts' hex, whose string order is the key's byte order                                                                                                                                                                                                                                                                                   |
| outline shape keys                     | `ink.outline_shape_key` (sha1 of a version-2 marshal)                                                                                                                                                                                                                                                                                                             | carried, never computed: the facts hold each glyph's key as Python computed it. A key is only compared for equality or orders pieces that are then compared for equality, and never reaches an output                                                                                                                                                                                                                    |
| the outline-level delta                | `InkComparator._piece_diff`, `config_diff`'s fallback when a placed piece has no grid picture, whose outline reprs reach `delta_digest`                                                                                                                                                                                                                           | not ported: the engine fails the unit with an error frame. No corpus text reaches it: every glyph a corpus text shapes is a grid-rectilinear outline placed on the grid, and only the combining marks, which no corpus text holds, are not                                                                                                                                                                               |
| the Junior tracking                    | `JuniorOracle.__init__` derives it from the `hmtx` tables and raises when the premise fails                                                                                                                                                                                                                                                                       | `write_init` constructs the oracle in the parent, so the premise fails there, and the init carries the tracking                                                                                                                                                                                                                                                                                                          |
| glyph names                            | `Shaper.glyph_name` reads the `TTFont` glyph order                                                                                                                                                                                                                                                                                                                | names from the facts' glyph order by glyph id                                                                                                                                                                                                                                                                                                                                                                            |
| verify keys                            | `keys[unit.unit_id] = _recompute_fragment(...)` in `_corpus_worker`: Python evaluates the right side first, so the key is the id the recomputation stamped                                                                                                                                                                                                        | key each verify reply by the recomputed id                                                                                                                                                                                                                                                                                                                                                                               |
| padding in the explain text            | `ExplainReport.render`'s `{candidate.stance:<16}` and its siblings pad by code points                                                                                                                                                                                                                                                                             | Rust's `{:<16}` pads by `char`, which agrees                                                                                                                                                                                                                                                                                                                                                                             |
| the in-process left                    | `kernel_exec.case_line` writes the left's record and the kernel's `cases::parse_left` reads it back                                                                                                                                                                                                                                                               | `LeftContext::letter(index, settled)` on the engine's own `Settled`, `LeftContext::boundary` after a boundary; the settle stage proves the round trip lost nothing                                                                                                                                                                                                                                                       |
| guard verdicts                         | `kernel_exec.guard_sweep`'s quantified map; `settle.guard_blocks` raises `KeyError` on a key it lacks                                                                                                                                                                                                                                                             | parse `ams_m1_kernel::guard::sweep` once at init into a shared map; a missing key is an error                                                                                                                                                                                                                                                                                                                            |
| engine modes                           | `kernel_exec.settlement_flags()` and the kernel's `engine_for` (`trace_memo: true`, default modes otherwise)                                                                                                                                                                                                                                                      | the init carries both mode booleans, and each thread builds its engines as `engine_for` does                                                                                                                                                                                                                                                                                                                             |
| the pack header                        | `SubsetPack.open` checks the format, `PACKER_DIGEST`, the byte order and the table digests                                                                                                                                                                                                                                                                        | the init carries the values Python would check, and the engine refuses a header that differs                                                                                                                                                                                                                                                                                                                             |
| the deflate                            | `build._FragmentPacker.pack`                                                                                                                                                                                                                                                                                                                                      | not a target; a zlib stream with the dictionary's Adler-32 that ends exactly where its span ends, which `_FragmentArena.read` requires                                                                                                                                                                                                                                                                                   |

## Dual-run harness and stage fixtures

### The dual run

`rebuild/tools/units_dual_run.py` runs the real Python worker and the Rust engine on the same units and compares them. It is the acceptance gate (decision 6) and the check each late task ends with.

- Sources: `--source mini` and `--source live`. The mini source is the hermetic mini bundle under `rebuild/review/fixtures/mini/`, with its spec materialized from `pin.json` as the `mini_bundle` fixture in `rebuild/conftest.py` does it, plus a copy of each of its units with `configs` set to `('ss10',)`, its `render_groups` and `config_classes` narrowed to that one configuration, which takes the unit's class, and an ordinal after the bundle's; `check_unit` requires the render groups to partition the configs. The mini's spec and pack are materialized at one path, `var/rust-port/mini-source/`, because a fragment's provenance pointers name rune files by their path under the spec root. The bundle's audit holds no `ss10` row, and the copies are what reach the isolated overlay, the `ss10` gate, the `ss10` subset rows and the Junior check on the mini. The live source is `rebuild/out/m1/` and `rebuild/out/review/`, read only, loaded through `build._load_corpus` as the profile's `prep.py` loads them. A live run takes `--per-config K` handouts from each first configuration's stretch of `_configuration_order`, so the `ss10` overlay and each stylistic set are covered, plus `--batches N` evenly spaced handouts, or `--all`.
- The Python side is real `build._corpus_worker` processes over real pipes, spawned as `_RecomputeRunner` spawns them, at `--python-jobs`. The Rust side is `units_exec.UnitsProcess` at `--threads`. Both sides take the same handouts, and each handout's results are compared once both sides have answered it and then dropped, so the harness holds a bounded number of handouts per side whatever the run's size. `--engines python,python` compares two Python runs, the second at another width and taking its handouts in reverse within a window of twice both sides' workers past the first handout not yet compared, so a run no longer than that window is fully reversed.
- Every `_UnitProjection` field except `packed` is compared by ordinal, type-strict (a tuple is not a list, a bool is not an int); each fragment is inflated from both sides with the build's dictionary and compared byte for byte; each batch's contract errors are compared as lists; and a deterministic sample goes through `verify` with injections built from the Python side's projections.
- The report is JSON under `var/rust-port/dual-run/`: the units compared and the loaded corpus's unit total, each field's disagreement count with its first examples (ordinal, codepoints, configurations, both values), and each side's wall time and peak footprint. Any disagreement, or a compared count short of the units handed out, exits 1. `--plant FIELD` corrupts one Rust-side value to show the comparator reports it.
- A live `--all` run is a heavy pass: detach it, watch its log (`doc/running-long-steps.md`), and run nothing else beside it.

### Stage fixtures

`rebuild/tools/units_stage_fixtures.py dump --source mini|live [--stage STAGE ...] --out DIR` runs the real worker in process over the dual run's sources and records, per stage, what the Python stage took and what it returned, in `DIR/STAGE.ndjson`; every dump also writes the units file, `units.ndjson`, one `batch` request frame per handout holding the unit records the `wire` stage reads, and `source.json`, which names the source and its handouts. A stage is a wrapper around the Python the stage table names, with that stage's generated inputs; the task that ports a stage adds both (§ Working rules). The wrappers record and return; they never change a value, and `selfcheck` proves it by comparing a wrapped run's projection digest with an unwrapped run's.

Values are recorded in a typed JSON encoding that `PyValue` reads and writes: `{"tuple": [...]}`, `{"list": [...]}`, `{"dict": [[key, value], ...]}`, `{"bytes": hex}`, `{"set": [...]}` for a set or frozenset with its members sorted, `{"record": [class name, [[field, value], ...]]}` for a dataclass or named tuple, and ints, strings, booleans and null as themselves. `compare` decodes both sides' values and compares them type-strictly, so a set compares as a set; a stage records a `repr` only where its output is itself a repr. Dumps live under `var/rust-port/stages/` and are never checked in. They carry no digest of the Python that wrote them: a task that changes the Python a stage records re-dumps that stage, and the dual run, which runs the live Python, is the gate.

`compare STAGE --stages DIR` builds the crate (`units_exec.ensure_built`), writes an init for the dump's source (`units_exec.write_init`), runs `ams-review stage STAGE --init=<init> DIR/STAGE.ndjson`, and reports every difference. Where a stage's input is the unit record, its Rust side recomputes the upstream values from it through modules that already pass their own stages, and `compare` checks those upstream values first, so a failure names the stage it starts in.

| Stage      | Input                                                                                                                                                                                                            | Expected output                                                                                                                | Python reference                                                                |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| `wire`     | the init with the files it names, the font facts among them, the unit records, and a generated verify frame                                                                                                      | the same values, re-emitted through the Rust readers                                                                           | `units_exec`                                                                    |
| `pyfmt`    | the values the worker hashes (the ink diffs and the cluster key's head under `repr`, the carry projection) and every fragment it writes, and a generated edge set                                                | `repr`, sort-keys JSON, indent-1 JSON with the fragment framing                                                                | CPython's `repr`, `json.dumps`                                                  |
| `yaml`     | every drafted policy record, and generated edge strings                                                                                                                                                          | the flow dump                                                                                                                  | `yaml.safe_dump` with `draft_policy`'s arguments                                |
| `shape`    | every `(font, text, features)` the worker shaped                                                                                                                                                                 | glyph names, clusters, positions                                                                                               | `Shaper.shape`                                                                  |
| `ink`      | the text and configurations; generated: every distinct `ss10` window of the source, put to the oracle directly                                                                                                   | `repr(diffs)`, the three flags, `ink_deltas`, the diffs digest; for the generated set, `junior_pieces` and the oracle's answer | `_ink_readings`, the flag lines of `_phase1_unit`, `JuniorOracle.approves`      |
| `settle`   | the code points and the first configuration, and each text `Enricher.formed_spans` forms                                                                                                                         | every position's trace fields, and the text's formed spans                                                                     | `Enricher.explain_units`, `Enricher.formed_spans`                               |
| `explain`  | the same, and the divergent positions                                                                                                                                                                            | the rendered and the filtered explain text                                                                                     | `ExplainReport.render`, `enrich._filter_explain`                                |
| `subset`   | each `(config, codepoints)` the enricher read; generated: each configuration's first and last window, and spellings of a window `pack_key` reads or refuses                                                      | the row, or none                                                                                                               | `SubsetPack.row`                                                                |
| `enrich`   | the unit record with its phase-1 flags; generated: real records with one `new` token replaced by another cell token, and real records given a subset row whose glyph names differ from the before font's shaping | every `EnrichedUnit` field, the mismatch lines, and the primary-unit projection                                                | `Enricher.enrich`, `primary_unit_projection`                                    |
| `scaffold` | the unit record, its `ink_deltas` and final class; generated: every non-empty subset of `ACCEPTANCE_CONFIGS`, in config order                                                                                    | the scaffold, the change group, the cluster; for the generated set, the gate and the note                                      | `unit_scaffold`, `assign_change_group`, `_cluster_id_from_repr`, `config_badge` |
| `expect`   | the site pages' pins, every drafted pin and any-of candidate, a generated malformed set                                                                                                                          | the parse or its error, and the replay's verdict                                                                               | `parse_expect`, `_expand_maybe_ligatures`, `_check_interpretation`              |
| `schema`   | every drafted policy record, and generated mutations                                                                                                                                                             | the verdict                                                                                                                    | the drafter's three checkers                                                    |
| `draft`    | the unit record                                                                                                                                                                                                  | the drafts as JSON                                                                                                             | `Drafter`                                                                       |
| `fragment` | the unit record                                                                                                                                                                                                  | the fragment's bytes, content key and id                                                                                       | `unit_to_json`, `_fragment_bytes`                                               |
| `check`    | every drafted fragment, and generated mutations of each predicate                                                                                                                                                | the error list                                                                                                                 | `check_unit(at=(DRAFTED,))`                                                     |

A disagreement a later task finds goes back to the stage that owns it: add the input to that stage's generated set in `units_stage_fixtures.py`, fix the module, and rerun that stage's `compare`.

## Working rules

- One task per agent, in dependency order. A task is done when its acceptance commands pass, `make review-rs-check` passes after any Rust change, and after any Python change `make prettier`, a bare `uv run pyright` and the targeted tests pass. A task that changed Python under `rebuild/` ends with one `make test-rebuild`.
- A task whose Adds name a stage adds that stage's wrapper and generated inputs to `units_stage_fixtures.py`, runs `selfcheck` and `dump --stage <stage>` on both sources, and then its `compare`.
- One gate or pool at a time across all agents (`doc/parallelism.md`): `make test-rebuild`, a live dual run and a `make review-cycle` never overlap. Run targeted tests with `uv run pytest <targets> -n auto --dist worksteal`. Detach the live `--all` dual run and watch its log.
- Never edit `rebuild/kernel-rs/` (§ Layout). Never write `rebuild/out/`; the tools read it.
- Batch the edits to `rebuild/review/units_exec.py`: each one moves the unit store's stamp and costs the next cycle a cold units phase.
- From the `opt-in-switch` task on, a change to any Python the stage table names ships with the mirrored crate change. `rebuild/test_units_exec.py`'s mini-bundle engine test, which covers the mini source's `ss10` copies, fails the rebuild suite until it does.

## Tasks

| Key                    | Title                                                    | Depends on                                             |
| ---------------------- | -------------------------------------------------------- | ------------------------------------------------------ |
| `stage-fixtures`       | Record the real Python worker's stages                   |                                                        |
| `dual-run-harness`     | Compare two engines unit by unit                         | `stage-fixtures`                                       |
| `crate-skeleton`       | Add the review crate beside the kernel crate             |                                                        |
| `wire-format`          | Define the boundary's records on both sides              | `dual-run-harness`, `crate-skeleton`                   |
| `pyfmt-repr-json`      | Reproduce Python's `repr` and `json.dumps`               | `wire-format`                                          |
| `pyfmt-yaml`           | Reproduce PyYAML's flow dump                             | `pyfmt-repr-json`                                      |
| `font-facts`           | Carry the outline intern's readings as font facts        | `wire-format`                                          |
| `harfbuzz-shaper`      | Shape through HarfBuzz                                   | `font-facts`                                           |
| `ink`                  | Read the ink and the Junior check                        | `harfbuzz-shaper`, `pyfmt-repr-json`                   |
| `settle`               | Settle in process                                        | `wire-format`                                          |
| `explain-render`       | Render the explain text                                  | `settle`                                               |
| `subset-pack`          | Read the mapped baseline pack                            | `wire-format`                                          |
| `enrich-geometry`      | Enrich: spans, divergences, the pair and the highlights  | `ink`, `settle`, `subset-pack`                         |
| `enrich-prose`         | Enrich: notation, summary, provenance and mismatch lines | `enrich-geometry`, `explain-render`                    |
| `scaffold-and-groups`  | Assign change groups and build the scaffold              | `enrich-prose`                                         |
| `expect`               | Parse pins and replay them against the after font        | `harfbuzz-shaper`, `pyfmt-repr-json`                   |
| `schema-check`         | Check records against the rune schema                    | `wire-format`                                          |
| `drafter`              | Draft the pin, the policy record and the any-of record   | `expect`, `schema-check`, `pyfmt-yaml`, `enrich-prose` |
| `fragment`             | Build, stamp and deflate the fragment                    | `scaffold-and-groups`, `drafter`                       |
| `check-unit`           | Run the drafting-time contract check                     | `fragment`, `expect`                                   |
| `unit-pipeline`        | Serve units end to end on one thread                     | `check-unit`                                           |
| `worker-loop`          | Serve batches from a thread pool                         | `unit-pipeline`                                        |
| `opt-in-switch`        | Put the Rust engine behind the switch                    | `worker-loop`                                          |
| `memory-constant`      | Measure the engine's memory and budget it                | `opt-in-switch`                                        |
| `full-corpus-dual-run` | Pass the dual run over the whole corpus                  | `memory-constant`                                      |

Every `compare` command below runs from the repository root as `uv run python -m rebuild.tools.units_stage_fixtures compare <stage> --stages <dir>`, written here as `compare <stage> <dir>`, against both `var/rust-port/stages/mini` and `var/rust-port/stages/live` (a live dump at `--per-config 1`). Each one passes when it reports no difference.

### `stage-fixtures`: record the real Python worker's stages

- Mirrors: `build._corpus_worker`'s per-unit calls, which the stage wrappers hook: `_phase1_unit`, `_ink_readings`, `Enricher.explain_units`, `Enricher.enrich`, the three drafts, `unit_to_json`, `_fragment_bytes`, `check_unit`, `Shaper.shape`.
- Adds: `rebuild/tools/units_stage_fixtures.py` with `dump` and `selfcheck`, the mini and live sources of § The dual run with the mini's `ss10` copies, the typed value encoding with every variant of § Stage fixtures, the stage registry the porting tasks add to, and the units file. `selfcheck` also round-trips every projection the run returns through the typed encoding, type-strict.
- Done when: `uv run python -m rebuild.tools.units_stage_fixtures selfcheck --source mini && uv run python -m rebuild.tools.units_stage_fixtures selfcheck --source live --per-config 1` exit 0, and `uv run python -m rebuild.tools.units_stage_fixtures dump --source mini --out var/rust-port/stages/mini` and the same with `--source live --per-config 1 --out var/rust-port/stages/live` have written the units file.

### `dual-run-harness`: compare two engines unit by unit

- Mirrors: `_RecomputeRunner._drive_phase1`, `_configuration_order`, `_handout_width`, `_FragmentArena.read`, and the profile's `verify_against_store.py`.
- Adds: `rebuild/tools/units_dual_run.py` as § The dual run describes, with the Python engine on both sides.
- Done when: `uv run python -m rebuild.tools.units_dual_run --source mini --engines python,python` exits 0, and `uv run python -m rebuild.tools.units_dual_run --source mini --engines python,python --plant ink_deltas` exits 1 naming `ink_deltas` and the planted unit.

### `crate-skeleton`: add the review crate beside the kernel crate

- Mirrors: `kernel_exec.cargo_build` and the kernel's `kernel-build` and `kernel-check` make targets.
- Adds: `rebuild/review-rs/Cargo.toml`, `Cargo.lock` seeded from the kernel's, `src/lib.rs`, and `src/main.rs`, whose `version` subcommand prints the wire format and the kernel's `SPEC_FORMAT`; `rebuild/review-rs/target/` in `.gitignore` and in `.markdownlint-cli2.yaml`'s ignores; `review-rs` in `rebuild/conftest.py`'s `collect_ignore`; `make review-rs-build` and `make review-rs-check` (`cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`); a `doc/testing.md` row for the crate; `rebuild/test_units_exec.py` with the lock-parity test.
- Done when: `make review-rs-check && uv run pytest rebuild/test_units_exec.py rebuild/test_build_code_closure.py rebuild/test_review_code_closure.py -n auto --dist worksteal` pass, and `git status --porcelain rebuild/kernel-rs` prints nothing.

### `wire-format`: define the boundary's records on both sides

- Mirrors: `_RecomputeRunner.__init__`'s `init`, `audit.Unit`, the stage dumps' typed encoding.
- Adds: `rebuild/review/units_exec.py` with `INIT_FORMAT`, `VOCABULARY`, `ENGINES` holding only `python`, `engine()`, `ensure_built()` under a relink lock in the crate's `target/` as `kernel_exec` holds one, `write_init` without the font facts, `encode_batch` and `encode_verify`; `build.py` takes the engine from `units_exec.engine()` and refuses one outside `ENGINES`; the tools take their encoders from `units_exec`; `units_stage_fixtures compare` and the `wire` stage. Rust: `src/wire.rs`, `src/pyfmt/mod.rs` with `PyValue` and its typed reader and writer, and `ams-review stage wire`.
- Done when: `compare wire` passes on both dumps, and `uv run pytest rebuild/test_units_exec.py rebuild/test_review_code_closure.py -n auto --dist worksteal` passes.

### `pyfmt-repr-json`: reproduce Python's `repr` and `json.dumps`

- Mirrors: CPython's `repr` for int, str, None, bool, tuple, list and dict; `json.dumps(sort_keys=True)`; `json.dumps(indent=1, ensure_ascii=True)` with `_fragment_bytes`' framing; `str.isprintable`, `str.isspace` and `str.isdigit`.
- Adds: `src/pyfmt/repr.rs`, `json.rs`, the generated `unicode_tables.rs`, `units_stage_fixtures unicode-tables`, and the `pyfmt` stage.
- Done when: `uv run python -m rebuild.tools.units_stage_fixtures unicode-tables --check` passes and `compare pyfmt` passes on both dumps.

### `pyfmt-yaml`: reproduce PyYAML's flow dump

- Mirrors: `yaml.safe_dump` as `Drafter.draft_policy` calls it, through PyYAML's representer, resolver and emitter.
- Adds: `src/pyfmt/yaml.rs` and the `yaml` stage.
- Done when: `compare yaml` passes on both dumps.

### `font-facts`: carry the outline intern's readings as font facts

- Mirrors: `ink.OutlineCache.shape_key`, `OutlineIntern.place`, `draws` and `cells`; the sets `JunctionClassifier.__init__` builds; the tracking `JuniorOracle.__init__` derives and its refusal; `Shaper.glyph_name`.
- Adds: `units_exec.write_font_facts` and the init's `font_facts` and `junior_tracking`; Rust `src/fontfacts.rs`, and the facts in the `wire` stage.
- Done when: `compare wire` passes on both dumps with the facts in the init.

### `harfbuzz-shaper`: shape through HarfBuzz

- Mirrors: `shaping.Shaper.shape` with its two assertions, `ink._MemoizedShaper` and `release_shape_memos`, `ink.features_for`, `ink.kern_neutral`.
- Adds: `build.rs` and `src/ffi.rs` as § HarfBuzz and outlines settles them, with the HarfBuzz version in `ams-review version`; `src/shape.rs` with a per-thread memo released per batch; the init's version check; the `shape` stage; and `rebuild/test_units_exec.py::test_harfbuzz_shapes_like_uharfbuzz_on_the_mini_texts` as § HarfBuzz and outlines describes it.
- Done when: `compare shape` passes on both dumps, `ams-review` refuses an init whose `harfbuzz` differs from its own version, and `uv run pytest rebuild/test_units_exec.py -n auto --dist worksteal` passes.

### `ink`: read the ink and the Junior check

- Mirrors: `InkComparator.named_run`, `run_ink`, `ink_pieces`, `pieces_identical`, `ink_identical`, `junior_pieces`, `config_diff` up to its picture path, `_piece_cells`, `_run_pictures` and `ink._picture_diff`; `delta_digest`; `JuniorOracle.approves`; `ConfigFold.effective`; `build._ink_readings` and the flag lines at the top of `_phase1_unit`.
- Adds: `src/ink.rs`, `src/config_fold.rs`, the error frame for a unit that needs `_piece_diff`, and the `ink` stage with its generated set, which puts every distinct `ss10` window of the source to `JuniorOracle.approves(('ss10',), text)` directly: phase 1 asks the oracle only about an `ss10`-only unit that is neither ink- nor picture-identical, which the live corpus does not hold.
- Done when: `compare ink` passes on both dumps.

### `settle`: settle in process

- Mirrors: `settle.tokens_from_codepoints`, `_ligature_order`, `form_ligatures`, `guard_blocks`, `isolated_overlay_traces`, `boundary_settled`, `model.isolated_overlay_active`; `kernel_exec.guard_sweep`, `settle_sequences` and `case_line` with the kernel's `cases::parse_left`, `settlement_flags`, and the kernel's `engine_for`; `explain.explain_many`, `_report_of`; `Enricher.explain_units`, `Enricher.formed_spans`.
- Adds: `src/settle.rs`, with one engine per thread per configuration and its memos released at each batch's end, and the `settle` stage.
- Done when: `compare settle` passes on both dumps: every position's settled record, deciding stage, prospect, joint tiebreak, ranked candidates, eliminations with their provenance pointers, runner-up and notes.

### `explain-render`: render the explain text

- Mirrors: `ExplainReport.render`, `settle.cell_label`, `model.feature_config_token`, `enrich._filter_explain`.
- Adds: `src/explain.rs` and the `explain` stage.
- Done when: `compare explain` passes on both dumps.

### `subset-pack`: read the mapped baseline pack

- Mirrors: `SubsetPack.open`, `row`, `_read_header`, `_header_disagreement`, `pack_key`, `SubsetRow`.
- Adds: `src/subset_pack.rs`, one read-only mapping the threads share, mapped through the C library's `mmap`, which `src/ffi.rs` declares beside HarfBuzz; the `str.isdecimal` table `pack_key`'s reading of `int` needs, in `pyfmt/unicode_tables.rs`; and the `subset` stage with its generated set.
- Done when: `compare subset` passes on both dumps, and a pack whose header disagrees with the init is refused.

### `enrich-geometry`: enrich: spans, divergences, the pair and the highlights

- Mirrors: `Enricher.enrich` up to the summary: the overlay, `formed_spans`, the junction and extension tuples, the subset row and its assertion, `_diff_codepoints` with the alias comparison, `_ink_visible_positions`, `_segment_pieces`, `_pick_pair`, `_secondary_pairs`, `_advance_mismatch_cell`, `_highlight`, `_spans_from_clusters`, `_covering`, `_pen_positions`, `parse_entry_extension`, `cell_token`, the boundary marks.
- Adds: `src/enrich.rs` and the `enrich` stage without its generated set.
- Done when: `uv run python -m rebuild.tools.units_stage_fixtures compare enrich --fields geometry --stages <dir>` passes on both dumps.

### `enrich-prose`: enrich: notation, summary, provenance and mismatch lines

- Mirrors: `notation`, `notation_tokens`, `text_entities`, `letter_display`, `rune_display`, `_summarize`, `_summary_clause`, `_decided_by`, `_short_provenance`, `_junction_phrase`, `_cell_description`, `_before_junction_at_codepoint_gap`, `_collect_provenance`, the two mismatch lines, `TraceReport`, `primary_unit_projection`.
- Adds: the rest of `src/enrich.rs`, and the `enrich` stage's generated set, which makes both mismatch lines fire: the live corpus fires neither.
- Done when: `compare enrich` passes on both dumps, every field.

### `scaffold-and-groups`: assign change groups and build the scaffold

- Mirrors: `change_groups.reroutes`, `unexplained_configs`, `set_gate_group`, `_height`, `_junction_group`, `assign_change_group`; `build.unit_scaffold`, `config_badge`, `_config_badge`, `_candidate_constraint_sets`, `_gate_clauses`, `_config_features`, `_config_class_note`, `_cluster_id_from_repr`.
- Adds: `src/change_groups.rs`, `src/scaffold.rs`, and the `scaffold` stage with its generated set, every non-empty subset of `ACCEPTANCE_CONFIGS` through `config_badge`: the corpus's config sets reach neither the three-clause gates nor the `only under:` fallback.
- Done when: `compare scaffold` passes on both dumps.

### `expect`: parse pins and replay them against the after font

- Mirrors: `test_shaping.parse_expect`, `_letter_to_qs`, `_expand_maybe_ligatures`, `_token_char_spans`, `_is_modifier_char`, `_token_input_char_count`; `pins._check_interpretation`, `_expected_base`; `shaping.row_for`, `junctions_for`, `last_glyph_covering`, `first_glyph_covering`; `JunctionClassifier.classify`; `drafts.expect_string`, `_token_for_span`.
- Adds: `src/expect.rs`, `src/classify.rs`, and the `expect` stage.
- Done when: `compare expect` passes on both dumps.

### `schema-check`: check records against the rune schema

- Mirrors: `spec_load._load_schema`, `_SchemaValidator`'s vocabulary and root-local references, and `for_definition(...).check` as `Drafter.__init__` builds its three checkers.
- Adds: `src/schema.rs` and the `schema` stage.
- Done when: `compare schema` passes on both dumps.

### `drafter`: draft the pin, the policy record and the any-of record

- Mirrors: `Drafter.draft_pin`, `validate_semantics`, `draft_policy` with `_policy_position`, `_new_join_side`, `_gained_extension_side`, `_junction_identical`, `_baseline_cell_pin`, `_window_for_side`, `_window_when`, `_baseline_exit` and `_before_junction_at_gap`; `draft_any_of`; `stylistic_set_value`, `features_dict`; the three `to_json` methods; `DraftError`.
- Adds: `src/drafts.rs` and the `draft` stage.
- Done when: `compare draft` passes on both dumps.

### `fragment`: build, stamp and deflate the fragment

- Mirrors: `build.unit_to_json`, `_fragment_bytes`, `_FragmentPacker.pack`, `patch_fragment`, `check_scaffold`, `check_stamp`, `_junction_records`; `unit_cache.carry_projection`, `carry_content_hash`, `base58_64`, `unit_id_for`.
- Adds: `src/fragment.rs`, `src/pack.rs` (a zlib backend with a preset dictionary), and the `fragment` stage.
- Done when: `compare fragment` passes on both dumps, and each Rust-deflated fragment inflates through `zlib.decompressobj(zdict=dictionary)` to the Python fragment's bytes and ends exactly at its span.

### `check-unit`: run the drafting-time contract check

- Mirrors: `build.check_unit` at `DRAFTED`, `_is_delta_digest`, `unit_cache.is_content_id`, `subset_pack.is_junction_token`, `audit.slim_fragment`, `audit.machine_approved`.
- Adds: `src/check.rs` and the `check` stage.
- Done when: `compare check` passes on both dumps.

### `unit-pipeline`: serve units end to end on one thread

- Mirrors: `build._phase1_unit` in its order (ink, flags, re-route, enrich, change group, draft, pack, check), `_UnitProjection`, `_keep_contract_errors`, `_phase1_batches`, `_recompute_fragment`, and `_corpus_worker`'s message loop with its `verify` branch.
- Adds: `src/unit.rs` (`Worker::new`, `run_batch`, `verify`), `src/service.rs` and the `units` subcommand of § The process at one thread; Python `units_exec.UnitsProcess`, `decode_batch` and `RustProjection`; the dual-run harness's Rust engine.
- Done when: `uv run python -m rebuild.tools.units_dual_run --source mini --threads 1 && uv run python -m rebuild.tools.units_dual_run --source live --per-config 1 --threads 1` exit 0, with the verify sample.

### `worker-loop`: serve batches from a thread pool

- Mirrors: `_RecomputeRunner`'s pooled driving: one batch in flight per worker, `stop_workers` included.
- Adds: the thread pool behind `--threads`, sharing the spec index, the guard verdicts, the facts, the HarfBuzz fonts, the pack mapping, the schema and the corpus index across threads, with one set of engines, one HarfBuzz buffer and one shape memo per thread.
- Done when: `uv run python -m rebuild.tools.units_dual_run --source mini --threads 4 && uv run python -m rebuild.tools.units_dual_run --source live --per-config 1 --batches 8` exit 0.

### `opt-in-switch`: put the Rust engine behind the switch

- Mirrors: `_RecomputeRunner.__init__`'s pooled branch, `_drive_phase1`, `verify`, `stop_workers`, `_record_corpus_pool`, `_load_recomputed`; `unit_cache.environment_stamp`.
- Adds: `rust` in `units_exec.ENGINES`, the engine branch of § Where Python consumes the replies, the `units_engine` stamp line, `engine=` on the units line, the `corpus-units-rs` pool record; in `rebuild/test_units_exec.py`, a mini-bundle test that Python and Rust builds write the same corpus files (by the comparison `rebuild/test_unit_cache.py`'s serial-versus-parallel test uses) and that the two engines agree on the mini source's `ss10` copies under the dual run's comparator, a test that the stamp line appears only under `rust`, and a test that every kernel module the crate names is stamped; a sentence in `doc/rebuild-design.md` §14 naming the crate.
- Done when: `uv run pytest rebuild/test_units_exec.py rebuild/test_review_code_closure.py rebuild/test_unit_cache.py rebuild/test_alphabet_switch.py -n auto --dist worksteal` passes, then `make test-rebuild` passes, run alone.

### `memory-constant`: measure the engine's memory and budget it

- Mirrors: `artifact_cycle.CORPUS_WORKER_BYTES`, `corpus_job_budget`, `_corpus_fit_terms`, `corpus_write_job_budget`; the corpus entries of `doc/parallelism.md`; the `corpus` row of `rebuild/tools/calibrate_budgets.py`.
- Adds: the harness's `--engines rust`, which runs the Rust engine alone, and `--memory`, which with a `--threads` list reports each run's peak footprint and fits a process and a per-thread figure to them; `CORPUS_UNITS_RS_PROCESS_BYTES` and `CORPUS_UNITS_RS_THREAD_BYTES` in `rebuild/tools/artifact_cycle.py`, their comment carrying the measurement; an engine argument to `corpus_job_budget` that fits threads under the Rust engine and keeps the Python engine's answer by default; the harness's default widths for a live run, each engine's from `corpus_job_budget` over half the box's memory, since both engines run at once; a `corpus-units-rs` row in `calibrate_budgets.py`; the `doc/parallelism.md` entry.
- Done when: `uv run python -m rebuild.tools.units_dual_run --source live --per-config 1 --batches 24 --engines rust --threads 1,4,max --memory`, run detached, reports each run's peak footprint with the fitted process and per-thread bytes, the constants sit at or above that fit by the margin their comment states, and `uv run pytest rebuild/test_memory_budget.py rebuild/test_artifact_cycle.py rebuild/test_calibrate_budgets.py -n auto --dist worksteal` and `markdownlint-cli2 doc/parallelism.md` pass.

### `full-corpus-dual-run`: pass the dual run over the whole corpus

- Mirrors: the whole units phase.
- Adds: only fixes, each in the module that owns it, with the failing input added to that stage's generated set.
- Done when: `uv run python -m rebuild.tools.units_dual_run --source live --all`, run detached at the harness's default widths, exits 0: every unit compared, the compared count equal to the loaded corpus's unit total, and no disagreement.

## What stays in Python

- The parent: load, plan, the unit store and the cache, the triage order, the whole-corpus passes (duplicate groups, primary-unit resolution, class promotion), the write and its `PATCHED` check, the sidecars and the manifest. Decision 7 keeps them, and they are the next port.
- The serial path (`--jobs 1`) and the in-process verification sample it runs. They are the reference the existing byte-identity tests compare pooled builds against.
- Everything derived once per build from the fonts or the pages: `ConfigFold`'s trigger derivation (`feature_reach.derive_fold`), the font facts and the Junior tracking (fontTools is the outline reference), the curs sets and the site corpus index. Each crosses as data.
- The outline-level delta (`InkComparator._piece_diff`), which no corpus text reaches; the engine refuses a unit that needs it.
- Spec loading and its dump (`spec_load`, `kernel_io`), which §14.1 of `doc/rebuild-design.md` keeps in Python.
- The ink-signature pass and its store, which run in the parent before the units phase.
- `--mode table-diff`, which never starts the units pool.
- The change groups' and the ledger's prose, which only the manifest reads.

## Open points

- The default flip, and with it retiring the Python twin, is the owner's decision once the full dual run has passed; it is outside this plan. Until then every units-pipeline edit is written twice, which is the cost §14.1 records for a Python twin of settlement.
- The HarfBuzz source is fetched by `build.rs` and pinned by digest (§ HarfBuzz and outlines). Checking the source in under `rebuild/review-rs/` instead removes the network from a fresh checkout's first build and adds the source to the history for good; that trade is the owner's to make.
- Once the engine is fast, the parent's serial loading of replies may bound the units phase. The dual-run report gives each side's wall time, and the `[t] review.build units` line gives the phase's; that measurement is what orders the parent's port.
- The manifest's Stage B `review_code` hashes `units_exec.py` but not the crate, so while the engine is opt-in, `make verdict-ready` judges a Rust-built corpus by the Python sources. The unit store's `units_engine` line keeps a crate edit from serving stale units.
