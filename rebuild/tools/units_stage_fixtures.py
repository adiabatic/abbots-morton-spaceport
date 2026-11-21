"""Record what the real Python corpus units worker's and corpus build's stages take and return, for the ports of the units recompute and of the build's parent to Rust (`rebuild/CORPUS-UNITS-PORT-PLAN.md` and `rebuild/CORPUS-PARENT-PORT-PLAN.md`, each § Stage fixtures).

A stage runs the worker or the build (`Stage.run`). The worker's stages (`STAGES`) are described below. The build's (`BUILD_STAGES`) take a build source, `--scenario S` beside `--source`: the scenario's one build, prepared over the source's base prior (`open_build_source`, which `prepare --source SOURCE --scenario NAME|all [--check]` runs alone). A build stage is a call stage, whose recorders record a hooked call (`BUILD_HOOKS`) as the worker's stages do, or a boundary stage (`Stage.kind`), whose recorder returns the build's state at a phase boundary as a `Boundary`: a line a unit, every unit on the mini and every `--every`th ordinal on live (`LIVE_EVERY` by default), and a line a column with the digest of its canonical bytes over every unit (`column_line`). `dump --scenario` writes `source.json`, the prior corpus as the build finds it (`prior/`), clones of what the build reads (`inputs/`), the units init with its spec dump (`init/`) and the Rust build's input for that run's values without descriptors (`build.json`, `build._rust_inputs` through `corpus_exec.write_input`), then runs `build_m1` in this process over a clone of the prior, at the harness's default width under the Rust units engine and one write job, with the selected stages recording (`build_dump`). `selfcheck --scenario` runs that build twice, plain and with every build stage recording, and fails unless the two output trees are identical (`build_selfcheck`). `compare` takes a build stage too (`build_compare`): it opens the dump's audit clone (`audit.AuditParts`), writes `build.json` again with those descriptors, runs the Rust stage with them inherited, after the compares of the upstream build stages a boundary stage follows, and reports as for a worker stage; `compare write --write-threads N` runs the `write` stage's Rust side at that width in place of the dump's.

The build's stages (`BUILD_STAGES`):

- `build-input` is a boundary stage that records no hooked call: `build_dump` records the values it writes `build.json` from, a line a field under `input:<field>`, and the size of each audit part the build holds under `audit.held`, which the Rust side reads through the descriptors it inherits.
- `sink` records each `output_sink.write_output`, `replace_output` and `note_rewritten` call the build makes (`_sink_call`), keyed by its origin, the call and a digest of its inputs: the target's name, what it `held` before the call, the `payload` as its kind and the sha256 of the bytes the call writes (a writer's read from the target once it has run, or the bytes the call kept), the `sha256` a writer is kept against, `keep_if_equal` and the state of the counts file the call found (`counts_file`); and as its outputs what the call `returned`, the `counts` lines it appended and what it left at the target, `after`. Every file's bytes are kept once under their sha256 in `sink.blobs/` beside the stage file (`SINK_BLOBS`), from which the Rust side sets each target up again in a scratch directory and makes the call itself. Its generated lines (`_sink_cases`) put every call and payload kind to missing, equal, differing, FIFO, directory and linked targets, with and without the compare, with each state of the counts file, and `output_sink.replacing`, which the build reaches only through `row_delta.StreamedPart`.
- `containers` records each zlib and gzip container the build writes into a file and each deflated fragment it reads back, keyed by its origin, its `framing` and a digest of its inputs, with its `output`: a `gzip.GzipFile` stream opened for writing into a file (`gzipfile`: its name, level and mtime, followed from its opening to its close, and each write's size), a `gzip.compress` member (`compress`: a locator block's or an index member's), a `unit_index._Members` sink (`members`: its member size and threads), a fragment's `unit_index.deflate_fragment` (`raw`) and an `inflate_fragment` that returns (`inflate-fragment`: the stored bytes and the length); each writer's output is the written bytes' sha256 and length, and a `read` line beside it reads those bytes whole as `gzip.GzipFile` does, and a deflated fragment's `inflate-fragment` line reads it back at its length. Every input is kept once under its sha256 in `containers.blobs/` beside the stage file (`CONTAINER_BLOBS`) and named by that digest and a length, the blob's first bytes. Its generated lines (`_containers_generated`) cover every name, level and mtime over inputs from nothing to past `INDEX_MEMBER_BYTES` cut into uneven writes, each member boundary, `inflate_fragment`'s refusals, the gzip reader's tolerances and refusals, the member readers' `zlib.decompressobj` loop (`member-read`, `_member_steps`) and `zlib.crc32` (`crc32`).
- `json-forms` records each `json.dumps` and `json.loads` call the parent's modules make (`JSON_PARENT_MODULES`, told by the caller's frame, `_json_parents_call`) but for the key-only read's frames to and from its processes (`JSON_PROCESS_FRAMES`), keyed by its origin, its `form` and a digest of its `input`: a write in the form its arguments name (`JSON_DUMPS_FORMS`: `default`, `indent`, `sorted`, `unicode` and `compact`) with the value it writes and, as its `value`, `("text", text)`; a read (`loads`) that returns with the `str` or `bytes` it reads and `("value", value)`. Its generated lines (`_json_forms_generated`) put every form to an edge set (`_json_dumps_edges`) and the reader to texts of each kind (`_json_loads_edges`), with `("refused",)` where Python refuses and where the Rust side refuses what Python stringifies or reads (`_json_dumped_text`, `_json_read`).
- `sample` records each `build._verification_sample` call (`_verification_sampled`), keyed by its origin, its `form` and a digest of its `input`, as the form `verification` with the `(cached, seed, size)` it draws from and, as its `value`, the id words it draws. Its generated lines (`_sample_generated`) draw from `random.Random` itself, seeded with strings whose key words `Random.seed` cuts differently (`SAMPLE_SEEDS`): `sample` over every `k` from -1 to one past the population, `("refused",)` where it raises, for populations on both sides of each `setsize` step (`_setsize`) and three populations around each step's edge; `getrandbits` at every width to 64; `randbelow` (`_randbelow_with_getrandbits`) at bounds around each word edge (`SAMPLE_BOUNDS`); and `verification` over populations around `VERIFICATION_SAMPLE` and its `setsize`.
- `row-delta-read` records each `row_delta.read_head`, `open_parts` and `check_segment_header` call the build makes, each read again by the recorder before the call runs, so a refusal is recorded too, keyed by its origin, its `form` and a digest of its `input`: `read-head` with the output and what its head's path holds and, as its `value`, `("none",)`, `("head", document)` (`Head.document`) or `("refused", message)`; `check-segment-header` with the header line, output, generation, number and merge and `("checked",)` or a refusal; and `open-parts` with the head's document, `sha256` and what each part's path holds, and `("opened",)` or a refusal, followed, for a head that opens and merges by place or is the units index's, by a `part-rows` line a part, its kind (`base`, `place` or `key`), output, generation, number and file, and a `merge` line over all of them, by key with each payload's key as `unit_index.index_key` gave it to the merge (`_merge_outcome`), each valued `("rows", count, sha256)` over its rows' typed encodings (`_rows_outcome`) or `("raised", class, message)`. A refusal's text leaves out what `json.loads` raised (`_JSON_DETAIL`). Every part's bytes are kept once under their sha256 in `row-delta-read.blobs/` beside the stage file (`ROW_DELTA_BLOBS`). Its generated lines (`_row_delta_generated`) put heads to every refusal of `parse_head` and `Part.parse` and every value `int` and `str` take leniently (`_head_cases`), header lines to every refusal of `check_segment_header` (`_segment_header_cases`), heads to missing, resized and rehashed parts (`_open_parts_cases`), bases, segments by place and by key to every row, header and gzip refusal (`_part_rows_cases`), merges by place and by key to superseded, revived and out-of-order rows (`_merge_cases`), and parts held as gzip members to every refusal of `row_delta._Inflating`, read as an `inflating` line a buffer of `INFLATE_READ_BYTES` at a time (`_inflating_cases`, `_inflated`).
- `row-delta-write` records each call the build makes to `row_delta`'s writers and the rules that route them, keyed by its origin, its `form` and a digest of its `input`, with what Python gave as its `value`: `places-between` with the bounds and the count and the places or what it raised; `row-line`, `row-length` and `segment-header` with the row or the header's fields and the line, its length or the header line; `write-part` with the part's name, gzip name, what its target held (`("missing",)` or `("file", sha256)`), its plain bytes, read back from the part it wrote, the cuts its `fill` wrote them in and the rows `fill` returned, `write-segment` with its output, generation, number, what its target held and its rows, read back from the segment it wrote, and `streamed-part`, a gzipped `row_delta.StreamedPart`'s `close`, with its name, header, what its target held and the bytes written past its header, each valued its part's document or what it raised, with what its target and its staging file hold after; `commit` with the head's document, the directory's entries by kind (`_entries`) and what the head's path held, valued the names it removed, the head file and whether the file was kept (its inode and modification time unchanged); `remove-unlisted` and `remove-staging` with their arguments and the directory's entries, valued the names removed; `writes-base`, `fold-due` and `fold-forced` with their arguments, valued the decision; `claim-fold` with the output, the cycle's run, what the claim file in the cycle's directory held and the output this process held its claim for there, valued the claim, the claim file and the holder after; and `with-segment` with the head's and the part's documents and the stamps, valued the head's document. Every file's bytes are kept once under their sha256 in `row-delta-write.blobs/` beside the stage file (`ROW_DELTA_WRITE_BLOBS`). Its generated lines (`_row_delta_write_generated`) cut places at the edges, around each power of 36, past 128 bits and from fractions `int(text, 36)` reads leniently or refuses (`_places_cases`), write parts under every kind of gzip name, past the gzip writer's buffer and with a failing `fill` (`_write_part_cases`), segments with rows out of place order (`_write_segment_cases`) and streamed parts closed and abandoned (`_streamed_cases`), commit heads over directories of listed, unlisted, near-miss, staging, linked and Unicode-digit names (`_part_entries`), decide the fold over segment counts around the cap and the overrun (`_fold_cases`), and claim the fold with no run, this run's claim and another run's (`_claim_cases`, `_claim_state`).
- `audit-layout` is a boundary stage that records the build's `audit.audit_layout` call (`_layout_found`, hooked where `build` imports it) under `layout`: what it finds, an `audit.AuditLayout` of the sections, the reason or none and the byte the base's rows start at, which the Rust side finds again over the audit its build input names, read through the descriptors it inherits. Its generated lines (`_audit_layout_generated`) are keyed by a digest of their input, the path messages name and each part's blob with its member index, and valued what `audit_layout` finds or `("raised", class, message)` for its `ValueError` and `MemberError`; every part's bytes are kept once under their sha256 in `audit-layout.blobs/` beside the stage file (`AUDIT_LAYOUT_BLOBS`). They cover single files (`_layout_plain_cases`: no header, headers that end in a carriage return, are not UTF-8 or are another, sections in order and out of it, a stray row at each place in a section, rows past several line probes, configurations that are not UTF-8) and audits under a head (`_layout_head_cases`: the mini audit as `oracle.AuditWriter` writes it, a plain base with segments held as gzip members, plain segments, a folded base, then segments and header members made by hand that fail each check).
- `stream` is a boundary stage that walks the audit again in lockstep (`audit_stream.lockstep`) over the descriptors, sections and family keys of the build's stream (`_stream_walked`, hooked where `build` imports `stream_workload`), a window a unit (`WalkedBoundary`): its record under `value` (`_stream_record`: the window, its rows as the walk yields them, its family lines, whether it holds siblings, each row's signature key as `%016x` and the input key of its lines in order), and how the walk ended under `end`, `("done",)` or `("raised", class, message)` for an `OrderBreak` or a `MemberError`; and, when the build runs its key-only read, that read's `audit_stream.window_ranges` call under `ranges` (`_ranges_split`): its count and each range with its walk, the windows it holds, the sha256 of their `(window, rows)` entries and how it ended (`_walk_digest`). Its generated lines (`_stream_generated`) are keyed by a digest of their input, each part's blob with its member index, the sections `audit.audit_layout` finds over them and the counts `STREAM_SPLITS`, and valued every window's record, how the walk ended, and the ranges each count splits the windows into with each range's walk, or what the split raised; every part's bytes are kept once under their sha256 in `stream.blobs/` beside the stage file (`STREAM_BLOBS`). They walk `audit-layout`'s generated audits that are in sorted sections, the stream's own (`_stream_plain_cases`: windows shared across configurations with and without siblings, every kind of break inside a section, windows that are not canonical, and a section past `audit_stream.READ_BYTES`; `_stream_head_cases`: segments that supersede, remove, revive and add rows, unknown configurations across parts, segment lines of every refused kind, a segment section past `audit_stream.SEGMENT_READ_BYTES`, member sections that break or fail, and a base held as members past a read), and two audits whose last section runs past its part (`_stream_truncated`).
- `signature-store` records the ink-signature store as the build loads, follows and writes it, each line keyed by its origin, its `form` and a digest of its `input`: `load`, each `unit_cache.SignatureArrays.load` call, with what the store's path held (`("missing",)`, `("directory",)` or `("file", sha256)`) and the stamp's value, valued `("none",)` or `("columns", count, keys, digests, header)` with the sha256 of the keys as 8-byte big-endian words and of the digests (`_store_columns`); `miss-note`, each `unit_cache.signature_miss_note` call, with what the path held and the stamp's value, lines and detail, valued `("note", note)`; `trace`, a store the stage follows from its making (`SignatureArrays()` or a load) through every call on it the stage names (`SIGNATURE_CALLS`, nested calls among them), recorded at each `unchanged`, with its origin and its calls, keys and digests as hex, valued the calls' results and its new lines' count and sha256 (`_store_lines`); and `write`, each `unit_cache.write_signature_arrays` call, on a pooled build's write thread or inline, with the store's trace up to its last `unchanged`, the stamp's header fields (`unit_cache._stamp_header`), the stamps and what the store's path and its staging file held, valued `("written", target, staging)` with what each holds after. Every store's bytes are kept once under their sha256 in `signature-store.blobs/` beside the stage file (`SIGNATURE_STORE_BLOBS`). Its generated lines (`_signature_store_generated`) load stores Python writes, short and past one read of the body, headers of every kind the load drops or passes, bodies of every kind of line it drops and gzip files it reads, drops or raises on (`_signature_load_cases`); note stores missing, unreadable, matching and moved in each of the ways a stamp's lines and detail record (`_signature_note_cases`); trace every call's cases, the merge of hits, misses and late misses and the stamps' `==` (`_signature_traces`); and write stores under a stamp with its lines and a bare value, with stamps and without, stamps that take the header's own keys, a staging file left behind, an empty store and one past the gzip writer's buffer (`_signature_writes`).
- `signature` records each ink signature the build shapes, keyed by its origin, its `form` and a digest of its `input` (`_shaped_line`): `pair`, each `(text, configuration)` pair a `build._shape_misses` call shapes, serially or on its pool, and `late`, each `(window, configuration)` a `build._LateShaper` call shapes rather than finds in the store, with the signature `ink.InkComparator.signature` gives, shaped again by the recorder over the call's fonts, as its `signature` and the digest the build gave, which must be that signature's `ink.signature_digest`, as its `value`. Its generated lines (`_signature_generated`) are every window of the source's audit (`_source_windows`) under each of `ACCEPTANCE_CONFIGS`, as the form `window`, shaped over the dump's units init's before and after fonts.
- `key-only` is a boundary stage that records the load's signature resolution where the build makes it: from `build._find_misses` (`_misses_found`), a unit a miss, `(key, section, window)` with the key as `%016x`, in miss order, the hit prior entries' positions as the `column:hits` line (`_hit_positions`), and the read under `read`, `("ran", processes, break)` or `("skipped",)` when the store's stamps held (`_read_value`); from `build._shape_found_misses` (`_found_misses_shaped`), the misses' digests in miss order as the `column:digest` line, read from the store's shaped misses (`_miss_digest`), and their count under `shaped`; and from `build._LateShaper.release` (`_late_released`), under `late`, the late shaper's count with the store's hits, misses and unhit entries and its new lines' count and sha256 once the stream has looked every row up (`_late_value`). Its generated lines (`_key_only_generated`) take the prior store the build would load with every other entry dropped, once with its stamps set to the build's, so the read is skipped and the late shaper shapes what the store lacks, and once with its audit identity moved, so the read runs with hits and misses; each is kept under its sha256 in `key-only.blobs/` beside the stage file (`KEY_ONLY_BLOBS`) and valued the same resolution as one tuple, each column as its count and sha256 (`_key_only_resolved`, which walks the audit as the stream asks for digests, `_late_walk`).
- `columns` records every call the build makes on `rebuild/review/columns.py`'s collections (`COLUMNS_CALLS`). A string table, tuple pool, mapping pool or key index is followed from its making (`_columns_made`, `_columns_called`), each call a line keyed by the collection's name, `run:<form>:<serial>`, and its place in the collection's calls, with the call as its `input` (a key index's making with the bytes of its column it reads, its width and its count) and, as its `value`, what the call returned beside the collection's size after it (`_columns_after`); the Rust side replays each collection's calls in that order. A window index is recorded over its state as each call finds it, since the stream sorts its ordinals in place after making it: a `window-index-state` line a state, keyed by the digest of its ordinals and keys and valued its `packed`, `windows`, `runs` and `nbytes` (`_window_state`), and a `window-index` line a call on it (`_window_called`). `window_word` and `sorted_by_word` calls are lines of their own, keyed by a digest of their input. Its generated lines (`_columns_generated`) intern empty values, repeats and values a pool refuses, seal a tuple pool, build key indexes over empty, colliding, repeated and malformed columns (`_key_cases`), build window indexes from packed and unpacked windows (`COLUMNS_WINDOWS`) as `window-index-build` lines and ask each, and its remapping, for its windows and others (`COLUMNS_ASKED`), and pack and sort words at the edges.
- `permutation` records each counting sort the build makes and the keys it orders by, keyed by its origin, its `form` and a digest of its `input`, with what Python gave as its `value`: `counting`, each `audit._counting_permutation` call, with the columns, the string table's strings in id order, the class order, the family ranks, the walk, the count, the runs it was given, read from a `columns.WindowIndex.runs` generator through a probe over the index's columns so the call still takes them whole (`_given_runs`), and each row's id word by ordinal (`_counting_inputs`); `triage`, each `audit._triage_permutation` call, with the table's class, group and window columns in place of the table (`_triage_inputs`); `triage-key`, each `audit.triage_key` call; and `family-ranks`, each `audit.family_ranks` call. Its generated lines (`_permutation_generated`) order the source's audit as the in-memory source loads it and a table whose units tie by the dozen (`_permutation_tables`) under the ledger's class order and a reversed one that leaves a class unranked, with no id words, id words that tie and id words by window (`_permutation_words`), by both sorts, walked in window order and in load order, with the runs and without; key every unit of both; and sort, key and rank at the edges (`_permutation_edges`).
- `merge` records each `audit._merge_members` call the build makes (`_members_merged`), keyed by its origin and a digest of its `input`: the window's members as given, each an `audit._Member` record with its config classes as a dict in their order (`_member_value`), the exempt classes sorted, the stats and each member's run of rows as `(merge key, config)` pairs, read from the call's `absorb` closure, the stream's keyed by section and the in-memory source's by config rank (`_member_runs`); with, as its `value`, whether any member was absorbed, the members in the order the call leaves them, the stats and each member's run once the call returns, or what it raised. Its generated lines (`_merge_generated`) record every call made by the stream over the source's audit under three ink digests and by the in-memory source's merge under two signatures (`_merge_sources`), and merge windows at the edges (`_merge_edges`) over runs of sections by the stream's `absorb` (`_section_absorb`).
- `workload` is a boundary stage that records what the build's stream returns (`audit.stream_workload`, hooked where `build` imports it, `_workload_streamed`), as `workload_boundary` lays it out: a unit a merged unit in load order, the unit `UnitTable.unit` materializes without a store beside its liveness, survivor, config rank, row of the id columns and input key (`_workload_unit`); a column each for the string table, the pools in id order, the tuple ranks, the window values, the window index's ordinals and keys, the pre-merge units and the compaction, and each class's ordinals (`UnitTable.rows_by_class`); and lines for the pools' sizes, the window index's shape, the merge's stats, the audit's rows, the ledger classes the units reach, the classes present and the change groups' counts.
- `premerge` is a boundary stage that records the pre-merge snapshot once the build rebases it onto the merge (`facts.PremergeSnapshot.rebase`, `_premerge_rebased`), as `premerge_boundary` lays it out: a unit a pre-merge unit in capture order, its row of the snapshot's columns with its grain as `PremergeSnapshot.grains` yields it; and lines for the identical classes and each column's length.
- `load` is a boundary stage that records what the build's load gives the plan (`build._load_corpus`, `_corpus_loaded`), as `load_boundary` lays it out: a unit a merged unit in load order as the `workload` stage holds it, a column over the pre-merge snapshot's rows, and lines for the source the load took, the key-only read's facts, the ink-signature store's counts and new lines, the stamps, the signature stamp, the merge's stats, the held audit's parts and the console lines the load printed, read from standard error while it ran.
- `store-records` records each unit store record the build parses, writes and slices, and each call it makes on a record's key or id, keyed by its origin, its `form` and a digest of its `input`, with what Python gave as its `value`: `parsed`, each `unit_cache._parsed_cached_unit` call, with the record and the parts it trusts, valued `("parsed", record)`, `("unheld",)` for a record holding a type `CachedUnit.to_record` never writes (`_parsed_held`), or, for a generated record, `("raised", kind)`, `unreadable` for what `unit_cache.StoreUnreadable` wraps and `failed` for what fails the build (`_parsed_outcome`); `record-line`, each `unit_cache.record_line` call, with its `CachedUnit` and the line; `prior-record`, each `unit_cache.prior_record_or_none` call, with the line and the `PriorRecord`, None or `("unheld",)` (`_prior_value`); `named`, each `unit_cache.named_ordinal` call, with the line and the key it names, read by the same function over `_KeyProbe` in place of the build's index; and `id-word`, `primary-units-crc` and `content-id`, each `unit_cache.id_word`, `primary_units_crc` and `is_content_id` call, with its argument and what it gave. Its generated lines (`_store_records_generated`) parse, write, slice and name a synthetic record (`_synthetic_record`) and a sample of the dump's prior store (`_sampled_records`), put each form to its edges over the synthetic record (`_parsed_edges`, `_record_line_edges`, `_prior_record_edges`, `_named_edges`), and put ids, primary units and values to the edges of the three calls.
- `store-load` follows each `unit_store.UnitStore` the build makes from its making (`_unit_store_followed`), each call a line keyed by the store's name, `run:store:<serial>`, and the call's place: `new` with its size and the input-key column it takes, then each call `STORE_FOLLOWED` names, the loads (`load_projection`, its projection as `_projection_value` lays it out, and `load_cached`), the prior records and their seal, the prior lines, the input-key index and the setters the whole-corpus passes and the write call, each with its arguments, valued what `_unit_store_after` reads of the store after it: a load's row and every accessor that takes only the ordinal (`_store_reading`), a setter's field read back. Its generated lines (`_store_load_generated`) replay each store the build made into a store of their own, checking every value against the build's, and continue the trace of each one whose rows were all loaded (`_store_script`): the id index and every id's ordinal, every row read, and its record line, `cached_as_is` and `predicted_unchanged` asked under its own fields and changed ones (`_patched_cases`), some rows' fields set and read again, the store emptied and each setter's refusal; then generated stores (`_store_cases`) load projections and records of every shape, put each refusal of a load to its text and each column to its width at both ends (`_projection_refusals`), and refuse a repeated id, a short input-key column and a repeated input key.
- `scan` is a boundary stage that records the plan's reads of the prior unit store, each where the build makes it: from `unit_cache.open_store`, the store as it opened under `open`, `("none",)` or `("opened", stamps, head)` with the head's document (`_store_opened`); from `unit_cache.scan_store`, walked again over handles of its own before the build reads a line (`_store_scanned`, `_reopened`), a unit a line in the store's materialized order, its place, the ordinal whose input key it names, its parsed record and the sha256 of its bytes (`_scanned_value`), and the `scan` line, whether it parses named lines, the parts it trusts and how it ended (`_scan_failure`); from `unit_cache.locate_prior_fragments`, the wanted records and where the walk placed them under `locate` (`_located_value`, id words as `%016x`); from `unit_cache.scan_marked`, scanned again before it runs, a `marked:<place>` line a record it parses and the `marked` line; from `unit_cache.LocatedFragments.find`, each placement the plan looks up under `find:` and the class and id; and from `unit_cache.store_miss_note`, `rederiving_moved` and `adopt_single_file`, the note, the decision and the head's document. Its generated lines (`_scan_generated`), on the mini only, open, scan and scan again at marked places the dump's prior store and stores made from it, under a segment, as a single file, cut short, corrupted, with no header line and with lines of every shape the scan skips or refuses, and heads and single files that do not open (`_scan_store_cases`); ask which parts recorded sizes trust, the manifest's identity over manifests of every kind, the miss note over stamps that match and that moved in each way it names, whether a re-deriving label moved, and adopt single files (`_scan_stamp_cases`); and walk the prior corpus with every store record wanted, through its store, its index and its parts as each is taken away or does not read, and a corpus whose deflated fragment only its store places (`_scan_locate_cases`). Every file is kept once under its sha256 in `scan.blobs/` beside the stage file (`SCAN_BLOBS`).
- `plan` is a boundary stage that records what the plan gives the units phase, from `build._build_m1`'s locals as the plan's line is printed (`build._phase_timing`, `_plan_timed`), as `plan_boundary` lays it out: a unit a row of the unit store as the `store-load` stage reads one, a column over the prior records the store keeps by id and one over the recomputed ordinals, and lines for the store plan, the prior shards, the count reused, the verification sample with the units materialized for it, the plan's note and the console lines the plan printed, read from standard error between the load's line and the plan's.
- `units` is a boundary stage that records the unit store once phase 1 has loaded every recomputed unit (`build._RecomputeRunner.phase1`, `_units_drafted`), as `units_boundary` lays it out: a unit a row of the store as the `store-load` stage reads one, a source in the arena as its part, its length and the sha256 of its deflated bytes, and lines for the drafting-time errors the runner kept, sorted, the count and the last of the `[progress]` lines phase 1 printed, its other console lines, and the arena's fragment count, deflated bytes, shard bytes and note.
- `triage` is a boundary stage that records the whole-corpus passes from the change-group promotion to the batches, from `build._build_m1`'s locals once `audit.assign_batches` returns (`_batches_assigned`), as `triage_boundary` lays it out: a unit its final class, unmatched group, exemplar flag, place in the triage index and batch, and lines for the classes in manifest order, each class's section in the order the build met the classes, a column over the triage order and the batch count.
- `passes` is a boundary stage that records the whole-corpus passes from the duplicate groups through the stale check, from `build._build_m1`'s locals as the units phase's line is printed (`build._phase_timing`, `_passes_timed`), as `passes_boundary` lays it out: a unit its duplicate group, cluster and primary units, and lines for the duplicate-group count, a column over the classes' rows, the secondary-junction counts, the verification's keys, sorted, the re-settlement mismatches and the units phase's note. Its generated lines (`_passes_generated`) put duplicate-group keys over one configuration and several, a judged pair and None, and text `repr` quotes and escapes to `unit_cache.duplicate_group_id_for`.
- `route` is a boundary stage that records how the write routes each class and cuts the runs, from `build._write_corpus`'s locals once `build._write_runs` returns (`_runs_cut`), as `route_boundary` lays it out: a unit whether its fragment is predicted unchanged, and lines for each class in shard order with its route, prior parts and manifest record, a column over each class's ordinals, the width and the runs, and whether the previous sidecars are copied forward. Its generated lines (`_route_generated`) cut runs over class sizes and widths at the edges, read the previous corpus's parts from manifests of every shape, and ask whether the dump's prior corpus holds a current units index, app index and locator.
- `shards` records each class the write's shard writer (`build._ShardWriter`) writes, from its `open` to its `close`, under `class:<id>`: its input the cap (None, the build's `SHARD_PART_BYTES`), the parts the class appends to as the build's directory held them when it opened, and each call made on the writer (`open`, `holds`, `keep`, `add_bytes`, which `add` makes too, and `close`) with its arguments, a fragment as the sha256 of its bytes; its value each call's result, the close's parts, total and pending renames with each staged part's size and sha256, and the counters the class moved (`_shard_closed`). Every fragment and held part is kept once under its sha256 in `shards.blobs/` beside the stage file (`SHARDS_BLOBS`), from which the Rust side makes the held parts again, as clones, in a scratch directory and replays the calls there. Its generated lines (`_shards_generated`, `_shard_cases`) replay writers of their own under small caps through `_shard_replay`, which also takes, commits, aborts and lists what a writer staged, and deletes staged parts by class ids that are wildcards themselves (`build._discard_runs`).
- `prior-read` records each read the write makes of a fragment it emits, keyed by its origin, its `form` and a digest of its `input`: `read`, `read-bytes` and `same-bytes`, each `unit_cache.PriorFragmentReader.read`, `read_bytes` and `same_bytes` call (`_prior_reading`), with the part it opens as the reader's directory held it (`_prior_held`), its address (`_prior_located`) and, for `same-bytes`, the body it compares, valued `("fragment", value)`, `("bytes", sha256)` or `("same", flag)` (`_prior_given`); and `arena`, each `build._FragmentArena.read` call (`_arena_read`), with the arena's dictionary, the span's bytes and the address, valued `("fragment", value)`. Every part, body, span and dictionary is kept once under its sha256 in `prior-read.blobs/` beside the stage file (`PRIOR_READ_BLOBS`), from which the Rust side makes each line's parts again in a scratch directory, or places its span at its start in an arena of its own, and makes the read there. Its generated lines (`_prior_read_generated`, `_prior_read_cases`) read plain and deflated fragments, as bytes and compared, under their own address and moved ones, and arena spans under their own dictionary and others, through every refusal of the reader's, each refusal valued `("raised", "ValueError", message)`, `("os-error", errno)` or, where `json.loads` refuses the bytes, `("undecoded",)` (`_prior_outcome`).
- `emit` records each unit the write receives, each `build._emission` call (`_emitted`), under `unit:<ordinal>`: its kind, `fragment` for a fragment the write serializes or `bytes` for a cached fragment's stored bytes, its unit id, content key and policy file, the sha256 of the bytes the write takes from it, the config note the write keeps for it, and, for stored bytes, their address and the identity that stands in for the fragment (`_emission_value`). The Rust side runs the build through the routing and emits every unit of every class in shard order.
- `check-patched` records each fragment the write checks, `check_unit` at `PATCHED` as `build._CorpusCheck.unit` calls it (`_patched_checked`), under `patched:` and a digest of the fragment, as the `fragment` and, as its value, the error list. Its generated lines (`_check_patched_generated`), under `generated:` and the same digest, mutate the smallest patched fragment of each shape (`_patched_shape`), generically over the keys the check reads (`PATCHED_DEPTHS`) and once or more for each predicate (`_patched_extras`), and record the error list, or the class of the exception the check raises, as `{"raised": name}`.
- `corpus-check` records the write's corpus check (`build._CorpusCheck`) as its checkers run, fed and folded. Each chunk a run checker hands over (`take_facts`) is a line under `chunk:`, the class's id and the units of it fed before: the class record it opened (`class_start`) or None, each unit it was fed (`unit`) with whether the cache supplied it, whether it closed the class (`class_end`), and the ids among its units the checker keeps identities for (`identity_for`), or None when it keeps every unit's; valued the facts it handed over, `_CorpusCheck._FACTS` by name without the underscore (`_check_facts`). Each `finish` of the checker that folds the chunks (`absorb`) is a line under `finish:`, with the chunk keys it absorbed in order and the manifest, valued the facts it folded and the errors `finish` returns. The Rust side feeds the chunks in order to one checker over the build input's settings and folds the named chunks into a fresh one for each finish. Its generated lines (`_corpus_check_generated`) finish each folding again over the manifest with each predicate broken (`_manifest_mutations`), under `generated:finish:`, and run small cases of their own through a run checker and a folding one (`_check_cases`, over `_check_base`), under `generated:case:` with their settings, chunks and manifest, each predicate made to fail, to pass a value Python reads as equal and to raise, a raise valued `{"raised": name}`.
- `sidecar-lines` records each run of the write's sidecar lines (`build._SidecarLines`) under `run:<n>`: its `open` (`_sidecars_opened`), with whether it copies the previous sidecars forward, valued whether each cursor opened to lines; each top-level `unit` and `cached` call (`_sidecar_unit`, `_sidecar_cached`) under `call:<n>`, with the fragment, or the cached unit's id, stored bytes, inflated length and identity, and the span and place, valued the events the call made in order, each cursor's `take` (`unit_index.LineCursor.take`) with the id and what it returned and each line written to a sidecar (`gzip.GzipFile.write`, `app_index.LocatorBlocks.add`), as lengths and sha256s, beside the run's counts after it; and its `close` (`_sidecars_closed`), valued the counts, the locator blocks' table entries and the digests of the lines each sidecar holds. Fragments, stored bytes and corpora are kept once under their sha256 in `sidecar-lines.blobs/` beside the stage file (`SIDECAR_LINES_BLOBS`). The Rust side replays each run's calls over the dump's prior corpus, or a generated run's own corpus made again from the blobs. Its generated lines (`_sidecar_lines_generated`) replay each build run that copied lines forward over corpora without copy-forward, without either sidecar, with a gap in either, with an index segment and with an index cut short (`_sidecar_variants`); project fragments with each field dropped and set to each kind of value (`generated:line:`); copy lines forward through every seam's edge (`generated:copy:`); take from cursors over sidecars that are missing, empty, not gzip, cut short or followed by other bytes, and over the prior's index as a head, a single file and without its head or its part (`generated:cursor:`); and cut locator rows into blocks across classes and block edges and refuse rows that do not ascend (`generated:locator:`).
- `manifest` is a boundary stage that records the manifest the write writes, from `build._prune_orphan_shards` as `build._write_corpus` calls it once the manifest is on disk (`_manifest_pruning`): the manifest's bytes, its identity (`unit_index.manifest_identity`), the shards pruned and the files under `units/` then. Its generated lines (`_manifest_generated`, `generated:prune:`) prune corpora of several shapes against manifests in both class forms.
- `index-write` is a boundary stage that records the units index the build writes, from `unit_index.write_index_lines` as `build._write_sidecars` calls it once the manifest is on disk (`_index_writing`): the head it committed, the files whose names start with the index's (`_index_write_files`), what the write did and its note. Its generated lines (`_index_write_generated`, `generated:<case>`) write the index over the corpora of `_INDEX_CASES`: first writes, the head alone, segments, folds claimed, held by another output and forced, heads, parts and single files that do not read, a single file adopted, staging and unlisted files, lines out of key order and a manifest that does not parse, each valued what the write did or raised, the files the corpus then holds and the counts lines.

`dump` runs `build._corpus_worker` in this process over a source and writes, under `--out`, the units file (`units.ndjson`: one `batch` request frame per handout, each frame as `units_exec.encode_batch` writes it), the source's description (`source.json`, which names the source and its handouts), and one `STAGE.ndjson` per selected stage. A stage is a `Stage` in `STAGES`: recorders on the worker's hook points (`HOOKS`), each of which sees a call's arguments before the call and its result after it and returns the fields of the line to record, and an optional generator of inputs the source does not reach. Every recorded value is written in the typed encoding (`units_exec.encode`, `decode`). The wrappers record and return; they never change a value. A line's `key` names it, and the stage's `outputs` are the fields that hold what the Rust side must reproduce.

`selfcheck` runs the worker over a source twice, once as the build runs it and once with every hook point wrapped and every registered stage recording into a temporary directory, and fails unless both runs give the same projection digest and every projection either run returns comes back from the typed encoding's round trip equal to itself, compared type-strictly (`same`).

`compare STAGE --stages DIR` builds the crate (`units_exec.ensure_built`), writes an init for the source the dump's `source.json` names (`source_init`, `units_exec.write_init`), runs `ams-review stage STAGE --init=<init> DIR/STAGE.ndjson`, and reports every key whose outputs the Rust answer does not reproduce, type-strictly, every key either side lacks, and every problem a stage's answer checks (`Stage.answer_checks`) find in the Rust answer's further fields. `--fields` narrows the outputs it checks to a comma-separated list of the stage's output fields and field groups.

The stages (`STAGES`):

- `wire` records each unit `_phase1_unit` receives, as the `audit.Unit` fields that cross (`unit_value`), under `unit:<ordinal>`; the Rust side reads them back from the units file. Its generated lines are the init's values with the spec's runes, the dictionary's bytes and the font facts, under `init`, and a verify frame with varied injections, under `verify`, which the line carries for the Rust side to read.
- `pyfmt` records what the worker hashes and writes, each line an `input` value, the `format` it is written in (`PYFMT_FORMS`) and the text Python wrote: the ink diffs' repr (`_ink_readings`), the repr of the cluster key's head, `(configs, class)` (`_cluster_id_from_repr`), the carry projection's sorted-key JSON (`unit_cache.carry_projection`) and each fragment's shard bytes (`_fragment_bytes`), keyed by the form and a digest of the value. Its generated lines put an edge set to each form (`_pyfmt_edges`).
- `yaml` records each record `Drafter.draft_policy` dumps, as the `input` its `yaml.safe_dump` call receives and the stripped text it ships, keyed by a digest of the record; a `safe_dump` call with other arguments than `draft_policy`'s (`YAML_DUMP_ARGUMENTS`) is refused. Its generated lines put an edge set to the same dump (`_yaml_edges`).
- `shape` records each call that reaches HarfBuzz, `Shaper.shape` under the worker's memo (`ink._MemoizedShaper`), as the `font` it shapes with (the resolved path `ink.shaper_for` registers it under), the `text` and the `features` it receives and the `ShapeResult` it returns, keyed by a digest of the call.
- `ink` records each unit `_phase1_unit` reads, under `ink:<ordinal>`, as its `text` and `configs` and, as its value, the repr of the diffs its `_ink_readings` call returns, the three machine flags, `ink_deltas` and the diffs digest from the projection. Its generated lines put every distinct text of a unit that holds `ss10` to the Junior oracle directly (`_ink_generated`), under `junior:` and a digest of the text, with the oracle's tracking-free Junior pieces and its answer for `ss10` alone.
- `settle` records each unit `Enricher.explain_units` settles, under `settle:` with its first configuration and its code points, as the `codepoints` and the `config` and, as its value, the `ExplainReport`'s code points, features and positions, each position's trace whole; and each text `Enricher.formed_spans` forms, under `spans:` and its code points, as the `codepoints` and the spans it returns.
- `explain` records each explain text `Enricher.enrich` writes, the `_filter_explain` call over the `ExplainReport.render` call just before it, under `explain:` with the report's configuration token (`model.feature_config_token`), its code points and the divergent positions, as the report's `codepoints`, that `config` and the `diff_positions` the filter receives, and as its outputs the `rendered` text and the `filtered` text the filter returns.
- `subset` records each lookup `Enricher.enrich` makes in the subset pack, `SubsetPack.row`, under `subset:` with its configuration and its code points, as the `config` and the `codepoints` it receives and the `SubsetRow` or None it returns. Its generated lines (`_subset_generated`), under `generated:` with the same two, look up each configuration's first and last window, the first window spelled each way `int(part, 16)` reads it (`_SUBSET_SPELLINGS`), its misspellings `pack_key` refuses (`_SUBSET_MISSPELLINGS`), strings that refuse or miss whatever the source (`_SUBSET_MISSES`), and a configuration the pack does not hold.
- `enrich` records each unit `Enricher.enrich` enriches, under `enrich:<ordinal>`, as the unit's `record` as a request frame carries it (`units_exec.unit_record`, written as one compact JSON line) and the phase-1 `flags` the worker set on it (`ink_identical`, `picture_identical`, `junior_equivalent`), and as its outputs every `EnrichedUnit` field but the unit itself, which is the input, the `mismatches` lines the call added to the enricher's list, and the unit's `primary_unit_projection`. Its generated lines (`_enrich_generated`) make both mismatch lines fire: a unit whose `new` has one cell token replaced, under `generated:new:<ordinal>`, and a unit enriched against a subset row with a renamed glyph, which the line carries as its `row`, under `generated:row:<ordinal>`. Its field groups (`compare --fields`) name `geometry`, the fields `Enricher.enrich` computes before its summary (`ENRICH_GEOMETRY`).
- `scaffold` records each unit `_phase1_unit` drafts, under `scaffold:<ordinal>`, as the unit's `record` as a request frame carries it, taken before phase 1 sets its flags and re-routes it, and the `ink_deltas` and the `final_class` (the change group, or None) the call drafts its fragment with, read from its projection, and as its value the `scaffold` that call's `unit_scaffold` returned, copied before `unit_to_json` gives it the final class, with the projection's `change_group` and `cluster`. Its generated lines (`_scaffold_generated`) put every non-empty subset of `ACCEPTANCE_CONFIGS` to `build.config_badge`, under `badge:` and the subset, as the `configs` and, as the value, the `gate` and the `note`.
- `expect` records each call to `drafts.expect_string`, under `string:` and a digest of the call, as the `codepoints`, `spans` and `junctions` it receives and, as its value, the expect string it returns; and each pin the drafter replays against the after font, the drafted pin through `Drafter.validate_semantics` and each any-of candidate `Drafter.draft_any_of` returns, replayed as the pin is, under `pin:` and a digest of the replay's inputs, as the `text`, the `expect` and the `features` and, as its value, the pin's parse or its error, its interpretations, the after font's row and the replay's verdict (`_expect_line`). Its generated lines (`_expect_generated`) add `expect_string` calls over every boundary and connector, and the replay of every pinned cell of the site pages and of an edge set of pins (`_expect_edges`).
- `schema` records each record the drafter's checkers check, `_SchemaValidator.check` on a `for_definition` checker, under `drafted:` and a digest of the call, as the `definition` the checker checks against and the `record` and, as its value, the verdict: whether `check` found no problem, which is all the drafter's output reads. Its generated lines (`_schema_generated`), under `generated:` and the same digest, check one drafted record of each shape and every policy record of the source's rune files against each of the drafter's definitions, and mutations of each against its own (`_schema_mutations`).
- `draft` records each unit `_phase1_unit` drafts a full fragment for, under `draft:<ordinal>`, as the unit's `record` as a request frame carries it, taken before phase 1 sets its flags and re-routes it, and the phase-1 `flags` the worker set on it, and as its value the fragment's `drafts`: the pin, the policy record or None, and the any-of record, each as its `to_json` writes it. A slim fragment, which the drafter skips, records nothing.
- `fragment` records each unit `_phase1_unit` drafts, under `fragment:<ordinal>`, as the unit's `record` as a request frame carries it, taken before phase 1 sets its flags and re-routes it, the phase-1 `flags` the worker set on it, and the `ink_deltas` and the `final_class` it drafts its fragment with, read from its projection, and as its value the fragment's shard bytes as the `_fragment_bytes` call its packer makes returned them (`fragment`, ASCII text), its `content_key` and its `id`. Its answer check (`_fragment_packed`) inflates the deflated fragment the Rust answer carries as `packed` through `zlib.decompressobj` with the source's preset dictionary, as `build._FragmentArena.read` inflates one, and requires the stream to end exactly where the bytes end and to give the Python fragment's bytes.
- `check` records each fragment `_phase1_unit` checks, `check_unit` at `DRAFTED`, under `drafted:` and a digest of the fragment, as the `fragment` and, as its value, the error list. Its generated lines (`_check_generated`), under `generated:` and the same digest, mutate the smallest drafted fragment of each shape (`_check_shape`), generically (`_check_mutations`) and once or more for each predicate (`_check_extras`), and record the error list, or the class of the TypeError, AttributeError or ValueError the check raises, as `{"raised": name}`.

`unicode-tables` writes `rebuild/review-rs/src/pyfmt/unicode_tables.rs`, the code point ranges this CPython's `str.isprintable`, `str.isspace`, `str.isdigit` and `str.isdecimal` accept, and with `--check` writes nothing and fails when the file differs from what it would write.

The sources (`open_source`):

- `mini` is the frozen mini bundle under `rebuild/review/fixtures/mini/`, its spec materialized from `pin.json` and its subset tables packed under `var/rust-port/mini-source/` (`_mini_source`), as the `mini_bundle` fixture in `rebuild/conftest.py` does it under its temporary root, followed by a copy of each of its units under `ss10` alone, with an ordinal after the bundle's (`_ss10_copy`). The bundle's audit holds no `ss10` row, and the copies are what reach the isolated overlay, the `ss10` gate, the `ss10` subset rows and the Junior check on the mini. Every handout is taken.
- `live` is the last cycle's corpus under `rebuild/out/m1/` and `rebuild/out/review/`, read only and loaded as the build loads it (`build._load_corpus`). A live run takes `--per-config K` handouts from the start of each first configuration's stretch of the pool's queue (`build._configuration_order`), `--batches N` evenly spaced handouts, or `--all`. It refuses a stale subset pack or ink-signature store rather than rebuild either, since `rebuild/out/` is read only here.

Handouts are `build.PHASE1_HANDOUT_UNITS` wide, cut from the queue in configuration order, and each is passed to the worker through multiprocessing's pickler, as a pool worker receives it.

Run from the repository root:

    uv run python -m rebuild.tools.units_stage_fixtures dump --source mini --out var/rust-port/stages/mini
    uv run python -m rebuild.tools.units_stage_fixtures dump --source live --per-config 1 --out var/rust-port/stages/live
    uv run python -m rebuild.tools.units_stage_fixtures selfcheck --source mini
    uv run python -m rebuild.tools.units_stage_fixtures compare wire --stages var/rust-port/stages/mini
    uv run python -m rebuild.tools.units_stage_fixtures unicode-tables --check
    uv run python -m rebuild.tools.units_stage_fixtures selfcheck --source mini --scenario incremental
    uv run python -m rebuild.tools.units_stage_fixtures dump --source mini --scenario warm --out var/rust-port/build-stages/mini-warm
"""

from __future__ import annotations

import argparse
import bisect
import collections
import contextlib
import copy
import dataclasses
import filecmp
import functools
import gzip
import hashlib
import importlib
import io
import inspect
import itertools
import json
import math
import os
import pickle
import random
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unicodedata
import warnings
import zlib
from array import array
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import batched, chain, combinations, cycle, product
from multiprocessing.reduction import ForkingPickler
from pathlib import Path
from typing import IO, Any, BinaryIO, TypedDict

import yaml

from rebuild.pipeline import fingerprint, oracle, output_sink, row_delta, spec_load
from rebuild.pipeline.explain import ExplainReport
from rebuild.pipeline.kernel_exec import KERNEL_ALLOCATOR_ENVIRONMENT
from rebuild.pipeline.model import feature_config_token
from rebuild.review import (
    app_index,
    audit,
    audit_stream,
    build,
    columns,
    corpus_exec,
    drafts,
    facts,
    ink,
    landing,
    unit_cache,
    unit_index,
    unit_store,
    units_exec,
)
from rebuild.review.audit import ACCEPTANCE_CONFIGS, BATCH_SIZE, Unit, UnitTable, load_ledger
from rebuild.review.drafts import Drafter, corpus_index_from_pages
from rebuild.review.enrich import LETTERS, EnrichedUnit, Enricher, load_spec, primary_unit_projection
from rebuild.review.fixtures.mini import pin
from rebuild.review.subset_pack import (
    PACK_NAME,
    SubsetPack,
    _header_disagreement,
    _read_header,
    key_codepoints,
    table_digests,
    write_pack_in_memory,
)
from rebuild.review.unit_store import UnitStore
from rebuild.review.units_exec import Record, compact, decode, encode
from rebuild.tools import console, cycle_paths, cycle_timings, memory_tally
from rebuild.validation.shaping import row_for

REPO_ROOT = build.REPO_ROOT
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
SOURCES = ("mini", "live")
UNITS_FILE = "units.ndjson"
SOURCE_FILE = "source.json"
SOURCE_FORMAT = "ams-review-stage-source/1"
SS10 = "ss10"
MINI_ROOT = REPO_ROOT / "var" / "rust-port" / "mini-source"

# The worker's per-unit calls a stage can record, by name: each is the module and the attribute path the wrapper replaces for the length of a run. Every caller reaches these through the module global or the class attribute, so a replacement installed before the worker starts sees every call.
HOOKS: dict[str, tuple[str, str]] = {
    "_phase1_unit": ("rebuild.review.build", "_phase1_unit"),
    "_ink_readings": ("rebuild.review.build", "_ink_readings"),
    "Enricher.explain_units": ("rebuild.review.enrich", "Enricher.explain_units"),
    "Enricher.formed_spans": ("rebuild.review.enrich", "Enricher.formed_spans"),
    "Enricher.enrich": ("rebuild.review.enrich", "Enricher.enrich"),
    "ExplainReport.render": ("rebuild.pipeline.explain", "ExplainReport.render"),
    "_filter_explain": ("rebuild.review.enrich", "_filter_explain"),
    "SubsetPack.row": ("rebuild.review.subset_pack", "SubsetPack.row"),
    "Drafter.draft_pin": ("rebuild.review.drafts", "Drafter.draft_pin"),
    "Drafter.draft_policy": ("rebuild.review.drafts", "Drafter.draft_policy"),
    "Drafter.draft_any_of": ("rebuild.review.drafts", "Drafter.draft_any_of"),
    "Drafter.validate_semantics": ("rebuild.review.drafts", "Drafter.validate_semantics"),
    "expect_string": ("rebuild.review.drafts", "expect_string"),
    "unit_to_json": ("rebuild.review.build", "unit_to_json"),
    "_fragment_bytes": ("rebuild.review.build", "_fragment_bytes"),
    "check_unit": ("rebuild.review.build", "check_unit"),
    "Shaper.shape": ("rebuild.validation.shaping", "Shaper.shape"),
    "_cluster_id_from_repr": ("rebuild.review.build", "_cluster_id_from_repr"),
    "carry_projection": ("rebuild.review.unit_cache", "carry_projection"),
    "yaml.safe_dump": ("yaml", "safe_dump"),
    "unit_scaffold": ("rebuild.review.build", "unit_scaffold"),
    "_SchemaValidator.check": ("rebuild.pipeline.spec_load", "_SchemaValidator.check"),
}


# --- the typed encoding -------------------------------------------------------------------------


def same(left: object, right: object) -> bool:
    """Whether two values are equal under the typed encoding: of one kind (a tuple is not a list, a bool is not an int, a record matches a record of its class name and fields), a dict's entries in one order, and a set's members as a set."""
    return compact(encode(left)) == compact(encode(right))


# --- the sources --------------------------------------------------------------------------------


@dataclass
class Source:
    """One source's worker `init` (as `_RecomputeRunner.__init__` builds it), its unit table and an empty unit store over it, as the plan leaves a recomputed unit, and the handouts the worker runs, each a run of ordinals of the pool's queue. An ordinal at or past the table's count names a mini unit's `ss10` copy (`_ss10_copy`). `corpus_units` is the loaded corpus's unit total, and `chosen` the handouts' indices in the queue's handouts, which is every index for the mini."""

    name: str
    init: dict
    table: UnitTable
    store: UnitStore
    handouts: list[array]
    corpus_units: int
    chosen: list[int]
    selection: dict

    def units(self, handout: Sequence[int]) -> tuple[Unit, ...]:
        """Materialize one handout's units as `_drive_phase1` does, with the mini's `ss10` copies made from the units they copy."""
        n = self.table.n
        return tuple(
            (
                self.table.unit(ordinal, self.store)
                if ordinal < n
                else _ss10_copy(self.table.unit(ordinal - n, self.store), ordinal)
            )
            for ordinal in handout
        )

    def unit_count(self) -> int:
        return sum(len(handout) for handout in self.handouts)

    def describe(self) -> dict:
        return {
            "format": SOURCE_FORMAT,
            "source": self.name,
            "selection": self.selection,
            "corpus_units": self.corpus_units,
            "handout_units": build.PHASE1_HANDOUT_UNITS,
            "handouts": self.chosen,
            "units": self.unit_count(),
        }


def _ss10_copy(unit: Unit, ordinal: int) -> Unit:
    """Return a copy of a mini unit under `ss10` alone: its configs, its render groups and its per-config classes narrowed to that one configuration, which takes the unit's class, at `ordinal`."""
    return dataclasses.replace(
        unit,
        configs=(SS10,),
        render_groups=((SS10,),),
        config_classes={SS10: unit.class_id},
        ordinal=ordinal,
    )


def _load_table(
    audit_path: Path,
    ledger_path: Path,
    spec_root: Path,
    after_font: Path,
    out_dir: Path,
    recompute_all: bool,
) -> tuple[UnitTable, UnitStore, frozenset[str]]:
    """Load a corpus's units as `_build_m1` loads them (`build._load_corpus`, at one signature job) and return the table, an empty unit store over it, and the ledger's `ink_identical` class ids. Unless `recompute_all`, the ink-signature store under `out_dir` must match this checkout: a stale one would have the load shape every window again, and its refusal names why."""
    ledger = load_ledger(ledger_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(spec_root)
    family_keys, helpers_digest = unit_cache.family_content_keys(spec_root, spec, after_font)
    keyer = unit_cache.UnitKeyer(family_keys, dict(LETTERS))
    identical_classes = frozenset(entry.id for entry in ledger if entry.ink_identical)
    if not recompute_all:
        environment = unit_cache.signature_environment(REPO_ROOT, build.SITE_BEFORE_FONT, helpers_digest)
        stale = unit_cache.signature_miss_note(out_dir, environment)
        if stale is not None:
            raise SystemExit(f"the ink-signature store under {out_dir} does not serve this checkout: {stale}")
    parts = audit.AuditParts(audit_path)
    try:
        loaded = build._load_corpus(
            parts,
            audit_path,
            ledger_path,
            ledger,
            dict(LETTERS),
            family_keys,
            keyer,
            out_dir,
            build.SITE_BEFORE_FONT,
            after_font,
            REPO_ROOT,
            helpers_digest,
            1,
            recompute_all,
            identical_classes,
        )
    finally:
        parts.close()
    table = loaded.workload.table
    return table, UnitStore(table.n, strings=table.strings, input_keys=loaded.input_keys), identical_classes


def _worker_init(
    after_font: Path,
    subset_dir: Path,
    subset_pack: Path,
    spec_root: Path,
    identical_classes: frozenset[str],
    *,
    before_font: Path = build.SITE_BEFORE_FONT,
    junior_font: Path = build.SITE_JUNIOR_FONT,
) -> dict:
    """Return the `init` dict `_RecomputeRunner.__init__` hands every pool worker, over the site's before and Junior fonts unless `before_font` and `junior_font` name copies of them."""
    site_pages = fingerprint.read_site_corpus(REPO_ROOT)
    return {
        "before_font": before_font,
        "after_font": after_font,
        "junior_font": junior_font,
        "subset_dir": subset_dir,
        "subset_pack": subset_pack,
        "repo_root": REPO_ROOT,
        "spec_root": spec_root,
        "identical_classes": identical_classes,
        "fold": build._config_fold(build.SITE_BEFORE_FONT, after_font),
        "corpus_index": corpus_index_from_pages(site_pages),
        "dictionary": build.FRAGMENT_DICTIONARY.read_bytes(),
    }


def _queue_handouts(queue: Sequence[int]) -> list[array]:
    return [array("I", handout) for handout in batched(queue, build.PHASE1_HANDOUT_UNITS)]


def _mini_source() -> Source:
    """Materialize the mini bundle's spec and pack its subset tables under `MINI_ROOT`, replacing what an earlier run left there, and load the bundle. The provenance pointers a fragment carries name rune files by their path under the spec root, so the root is the same path on every run, and a stage dump's fragments equal those of a later run over the same bundle."""
    shutil.rmtree(MINI_ROOT, ignore_errors=True)
    spec_root = pin.materialize(MINI_ROOT / "spec")
    subset_pack = spec_root / PACK_NAME
    write_pack_in_memory(MINI, ACCEPTANCE_CONFIGS, table_digests(MINI, ACCEPTANCE_CONFIGS), subset_pack)
    store_dir = MINI_ROOT / "out"
    store_dir.mkdir()
    after_font = MINI / "M1.otf"
    table, store, identical_classes = _load_table(
        MINI / "audit.tsv",
        spec_root / pin.LEDGER_PATH,
        spec_root,
        after_font,
        store_dir,
        recompute_all=True,
    )
    n = table.n
    ss10_rank = audit._config_index(SS10)
    ranks = [table.config_rank(ordinal) for ordinal in range(n)] + [ss10_rank] * n
    queue = sorted(range(2 * n), key=ranks.__getitem__)
    handouts = _queue_handouts(queue)
    return Source(
        name="mini",
        init=_worker_init(after_font, MINI, subset_pack, spec_root, identical_classes),
        table=table,
        store=store,
        handouts=handouts,
        corpus_units=n,
        chosen=list(range(len(handouts))),
        selection={},
    )


def _live_source(per_config: int, batches: int, everything: bool) -> Source:
    subset_dir = build.M1_SUBSETS
    subset_pack = subset_dir / PACK_NAME
    header, _ = _read_header(subset_pack)
    if header.get("configs") != list(ACCEPTANCE_CONFIGS):
        raise SystemExit(f"{subset_pack} packs {header.get('configs')}, not {list(ACCEPTANCE_CONFIGS)}")
    disagreement = _header_disagreement(header, table_digests(subset_dir, ACCEPTANCE_CONFIGS))
    if disagreement is not None:
        raise SystemExit(f"{subset_pack} is stale ({disagreement}); a cycle pass rewrites it")
    table, store, identical_classes = _load_table(
        build.M1_AUDIT,
        build.M1_LEDGER,
        REPO_ROOT,
        build.M1_AFTER_FONT,
        build.DEFAULT_OUT,
        recompute_all=False,
    )
    queue = build._configuration_order(array("I", range(table.n)), table)
    every = _queue_handouts(queue)
    width = build.PHASE1_HANDOUT_UNITS
    if everything:
        chosen = list(range(len(every)))
    else:
        picked: set[int] = set()
        stretch_start: dict[int, int] = {}
        stretch_end: dict[int, int] = {}
        for position, ordinal in enumerate(queue):
            rank = table.config_rank(ordinal)
            stretch_start.setdefault(rank, position)
            stretch_end[rank] = position
        for rank, start in stretch_start.items():
            first, last = start // width, stretch_end[rank] // width
            picked.update(range(first, min(first + per_config, last + 1)))
        if batches:
            step = len(every) / batches
            picked.update(int(index * step + step / 2) for index in range(batches))
        chosen = sorted(picked)
    return Source(
        name="live",
        init=_worker_init(build.M1_AFTER_FONT, subset_dir, subset_pack, REPO_ROOT, identical_classes),
        table=table,
        store=store,
        handouts=[every[index] for index in chosen],
        corpus_units=table.n,
        chosen=chosen,
        selection={"per_config": per_config, "batches": batches, "all": everything},
    )


def open_source(name: str, per_config: int = 0, batches: int = 0, everything: bool = False) -> Source:
    """Load the named source (the module docstring). The mini is always whole, so it refuses a selection; a live source needs one."""
    selected = bool(per_config or batches or everything)
    if name == "mini":
        if selected:
            raise SystemExit(
                "the mini source is always taken whole; --per-config, --batches and --all are live-only"
            )
        return _mini_source()
    if name == "live":
        if not selected:
            raise SystemExit("a live source needs --per-config K, --batches N or --all")
        return _live_source(per_config, batches, everything)
    raise SystemExit(f"unknown source {name!r}; the sources are {', '.join(SOURCES)}")


# --- the units file -------------------------------------------------------------------------------


def write_units_file(source: Source, out: Path) -> None:
    """Write the source's handouts as `batch` request frames, one compact JSON line each, to `out/units.ndjson`, and its description to `out/source.json`."""
    with (out / UNITS_FILE).open("w", encoding="ascii") as handle:
        for index, handout in enumerate(source.handouts):
            frame = units_exec.encode_batch(index, source.units(handout))
            handle.write(compact(frame) + "\n")
    (out / SOURCE_FILE).write_text(json.dumps(source.describe(), indent=1) + "\n", encoding="ascii")


# --- stages and the wrapped run -----------------------------------------------------------------


# The fields of a boundary stage's column line (`column_line`), which `compare` checks on a `column:` line in place of the stage's outputs.
COLUMN_FIELDS = ("values", "sha256")


def column_entry(value: object) -> bytes:
    """One value's canonical bytes in a column's digest: its typed encoding as one compact JSON line (`compact`, `encode`) and a newline."""
    return compact(encode(value)).encode("ascii") + b"\n"


def column_digest(values: Iterable[object]) -> tuple[int, str]:
    """How many values a column holds and the sha256 of its canonical bytes, each value's `column_entry`, in the column's order."""
    digest = hashlib.sha256()
    count = 0
    for value in values:
        digest.update(column_entry(value))
        count += 1
    return count, digest.hexdigest()


def column_line(name: str, values: Iterable[object]) -> dict[str, object]:
    """A boundary stage's line for one column, under `column:<name>`, with its count and digest (`column_digest`). The Rust side digests its column the same way."""
    count, digest = column_digest(values)
    return {"key": f"column:{name}", "values": count, "sha256": digest}


@dataclass(frozen=True)
class Boundary:
    """The build's state at one phase boundary, as a boundary stage's finisher returns it: `columns` over `count` units, each read by ordinal, and further lines, each with its own key, such as a column that does not hold one value a unit (`column_line`) or a phase note's facts."""

    count: int
    columns: Mapping[str, Callable[[int], object]]
    extra: Sequence[Mapping[str, object]] = ()

    def lines(self, every: int) -> Iterator[Mapping[str, object]]:
        """A line for every `every`th unit from ordinal 0, under `unit:<ordinal>` with each column's value there; a line a column with its digest over every unit (`column_line`); then the further lines."""
        for ordinal in range(0, self.count, every):
            yield {"key": f"unit:{ordinal}", **{name: read(ordinal) for name, read in self.columns.items()}}
        for name, read in self.columns.items():
            yield column_line(name, map(read, range(self.count)))
        yield from self.extra


@dataclass(frozen=True)
class WalkedBoundary:
    """A boundary whose units are too many to hold, such as the audit's windows, walked once in order rather than read by ordinal: `walk` gives each unit's value under every name of `columns`, in order, and appends its own further lines to the list it is handed once it ends. `lines` writes what `Boundary.lines` writes, a unit line for every `every`th unit, a line a column (`column_line`) and then the further lines."""

    columns: tuple[str, ...]
    walk: Callable[[list[Mapping[str, object]]], Iterator[Mapping[str, object]]]

    def lines(self, every: int) -> Iterator[Mapping[str, object]]:
        digests = {name: hashlib.sha256() for name in self.columns}
        ended: list[Mapping[str, object]] = []
        count = 0
        for values in self.walk(ended):
            if count % every == 0:
                yield {"key": f"unit:{count}", **{name: values[name] for name in self.columns}}
            for name, digest in digests.items():
                digest.update(column_entry(values[name]))
            count += 1
        for name, digest in digests.items():
            yield {"key": f"column:{name}", "values": count, "sha256": digest.hexdigest()}
        yield from ended


# A recorder sees one call's positional and keyword arguments before the call runs and returns None, to record nothing, or a finisher, which receives the call's result and returns what to record: for a call stage the fields of the line, a list of such fields for a call that records one line per item, or None; for a boundary stage a `Boundary` or a `WalkedBoundary`, or None.
Finisher = Callable[
    [object], Mapping[str, object] | list[Mapping[str, object]] | Boundary | WalkedBoundary | None
]
Recorder = Callable[[tuple, dict], Finisher | None]


# An answer check sees the source's worker `init`, a line's expected outputs and the value the Rust answer to the line carries under the check's field, decoded, and returns a problem or None.
AnswerCheck = Callable[[Mapping[str, object], Mapping[str, object], object], str | None]

# What a stage runs: the units worker over a worker source (`Source`), or the corpus build over a build source (`BuildSource`).
RUNS = ("worker", "build")
# How a stage records: a call stage a line a hooked call (or an item of its result), a boundary stage the build's state at a phase boundary (`Boundary`).
KINDS = ("call", "boundary")


@dataclass(frozen=True)
class Stage[S]:
    """One stage of the port: what it runs (`RUNS`) and how it records (`KINDS`), its recorders by hook name (`HOOKS` for the worker, `BUILD_HOOKS` for the build), its generated inputs, which give the fields of each further line for a source of its run, the fields of a line that hold its expected output, which `compare` checks against the Rust stage's answer to the line's `key`, named groups of those fields that `compare --fields` can check alone, and checks of further fields of a Rust answer that are not compared for equality, by field name."""

    name: str
    recorders: Mapping[str, Recorder]
    generated: Callable[[S], Iterator[Mapping[str, object]]] | None = None
    outputs: tuple[str, ...] = ("value",)
    field_groups: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    answer_checks: Mapping[str, AnswerCheck] = field(default_factory=dict)
    run: str = "worker"
    kind: str = "call"

    def __post_init__(self) -> None:
        if self.run not in RUNS or self.kind not in KINDS:
            raise ValueError(f"the {self.name} stage runs {self.run!r} and records {self.kind!r}")
        if self.kind == "boundary" and self.run != "build":
            raise ValueError(f"the {self.name} stage records a phase boundary, which only the build has")


def unit_value(unit: Unit) -> Record:
    """The `audit.Unit` fields that cross the boundary (`units_exec.UNIT_FIELDS`), as the worker holds them, in the unit's declaration order: what the Rust side's unit record must read back as."""
    return Record(
        "Unit",
        tuple(
            (name, dict(unit.config_classes) if name == "config_classes" else getattr(unit, name))
            for name, _wire in units_exec.UNIT_FIELDS
        ),
    )


def _wire_unit(args: tuple, _kwargs: dict) -> Finisher:
    unit = args[0]
    key, value = f"unit:{unit.ordinal}", unit_value(unit)
    return lambda _result: {"key": key, "value": value}


# How many units of the first handout the `wire` stage's verify frame carries.
WIRE_VERIFY_UNITS = 6


def _wire_generated(source: Source) -> Iterator[Mapping[str, object]]:
    """The `wire` stage's generated lines: the init's values as `units_exec.init_values` gives them, with each rune of the spec (its codepoint, sequence and stance names), the preset dictionary's bytes and the font facts (`units_exec.font_facts`), under `init`; and one verify frame over the first handout's first units, each with an injection that varies every field's kind, under `verify`."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(Path(source.init["spec_root"]))
    runes = {name: (rune.codepoint, rune.sequence, tuple(rune.stances)) for name, rune in spec.runes.items()}
    fonts = {side: source.init[key] for side, key in units_exec.FONT_SIDES}
    yield {
        "key": "init",
        "value": units_exec.init_values(source.init)
        | {
            "spec": runes,
            "dictionary": source.init["dictionary"],
            "font_facts": units_exec.font_facts(fonts),
        },
    }
    items: list[tuple[Unit, tuple]] = []
    for index, unit in enumerate(source.units(source.handouts[0][:WIRE_VERIFY_UNITS])):
        primary_units = tuple(
            (None if slot % 2 else f"{unit.input_key[:8]}-{slot}", slot == 1) for slot in range(index)
        )
        items.append(
            (
                unit,
                (
                    None if index % 2 else f"duplicate-{index}",
                    unit.cluster if index % 2 else f"cluster-{index}",
                    unit.class_id,
                    primary_units,
                ),
            )
        )
    yield {
        "key": "verify",
        "frame": compact(units_exec.encode_verify(items)),
        "value": [(unit_value(unit), injection) for unit, injection in items],
    }


# The forms the `pyfmt` stage checks, by the name a line's `format` gives: CPython's `repr`, the carry projection's sorted-key JSON (`unit_cache.carry_projection`), and a fragment's shard bytes (`build._fragment_bytes`).
PYFMT_FORMS: dict[str, Callable[[Any], str]] = {
    "repr": repr,
    "json-sorted": lambda value: json.dumps(value, sort_keys=True),
    "json-fragment": lambda value: build._fragment_bytes(value).decode("ascii"),
}


def _pyfmt_line(form: str, value: object, text: object) -> dict[str, object]:
    """One `pyfmt` line from the run, keyed by its form and a digest of the value's typed encoding, so a value the worker writes again gives the same line under the same key (`_Sink.write`)."""
    digest = hashlib.sha1(compact(encode(value)).encode("ascii")).hexdigest()[:20]
    return {"key": f"{form}:{digest}", "format": form, "input": value, "value": text}


def _pyfmt_ink_readings(_args: tuple, _kwargs: dict) -> Finisher:
    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, tuple)
        return _pyfmt_line("repr", result[0], repr(result[0]))

    return finish


def _pyfmt_cluster_head(args: tuple, _kwargs: dict) -> Finisher:
    head = (tuple(args[0]), args[1])
    return lambda _result: _pyfmt_line("repr", head, repr(head))


def _pyfmt_carry_projection(args: tuple, _kwargs: dict) -> Finisher:
    projection = {
        key: value for key, value in args[0].items() if key not in unit_cache.CARRY_PRESENTATION_KEYS
    }
    return lambda result: _pyfmt_line("json-sorted", projection, result)


def _pyfmt_fragment_bytes(args: tuple, _kwargs: dict) -> Finisher:
    fragment = args[0]

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, bytes)
        return _pyfmt_line("json-fragment", fragment, result.decode("ascii"))

    return finish


def _printable_boundaries() -> str:
    """Every code point beside an edge of a `str.isprintable` run, outside the surrogates: the characters where a wrong printable table would first show in a repr."""
    points: set[int] = set()
    previous = False
    for point in range(sys.maxunicode + 1):
        printable = chr(point).isprintable()
        if printable != previous:
            points.update((point - 1, point))
        previous = printable
    return "".join(chr(point) for point in sorted(points) if not 0xD800 <= point <= 0xDFFF)


def _pyfmt_edges() -> dict[str, list[object]]:
    """The values each form's generated lines put to it: the escapes and the quoting choices, every ASCII and Latin-1 character, characters beyond the BMP, empty and one-element containers, keys that sort differently by code point than by UTF-16, and the printable table's edges."""
    escapes = (
        "".join(chr(point) for point in range(0x20)) + "\"'\\/\x7f\x80\xa0\xad\u2028\ue650\uffff\U0001f600"
    )
    texts: list[object] = [
        "",
        "a",
        "'",
        '"',
        "'\"",
        "it's",
        'say "hi"',
        "\\",
        "\t\n\r",
        escapes,
        "".join(chr(point) for point in range(0x80, 0x100)),
        "e\u0301\u3000\U000e0001\U0010ffff",
        _printable_boundaries(),
    ]
    scalars: list[object] = [0, 1, -1, 2**63 - 1, -(2**63), True, False, None]
    containers: list[object] = [
        (),
        (1,),
        ("a",),
        ((),),
        (1, "a", None, True),
        ((((0, 1), (2, 3)), (), 0), ((), ((5, -1),), -2)),
        [],
        [()],
        ([], [1, [2, (3,)]]),
        {},
        {"a": (1, -2)},
        {"k": [], "j": {}},
        {1: "a", (2,): None, None: [True]},
    ]
    keyed: list[object] = [
        {},
        {"b": 1, "a": 2, "B": 3, "\xe9": 4, "\uffff": 5, "\U0001f600": 6, "\ue650": 7, "": 8},
        {"z": {"y": [], "x": {}}, "a": [1, (2, 3), None, True, False, -7]},
        {"s": escapes, "t": ("x", ["y", {"b": None, "a": 0}])},
        {"deep": {"a": [{"b": [{"c": []}]}]}, "n": -(2**63)},
    ]
    return {
        "repr": scalars + texts + containers + keyed[1:],
        "json-sorted": keyed + [["a", {"b": 1, "a": 0}], "x\u2028", 5, None, False],
        "json-fragment": keyed,
    }


def _pyfmt_generated(_source: Source) -> Iterator[Mapping[str, object]]:
    for form, values in _pyfmt_edges().items():
        for index, value in enumerate(values):
            yield {
                "key": f"{form}:edge:{index}",
                "format": form,
                "input": value,
                "value": PYFMT_FORMS[form](value),
            }


class _DumpArguments(TypedDict):
    default_flow_style: bool
    width: int
    allow_unicode: bool
    sort_keys: bool


# The arguments `Drafter.draft_policy` passes `yaml.safe_dump`, the only dump the `yaml` stage checks.
YAML_DUMP_ARGUMENTS: _DumpArguments = {
    "default_flow_style": True,
    "width": 10**6,
    "allow_unicode": True,
    "sort_keys": False,
}


def yaml_dump(value: object) -> str:
    """The text `Drafter.draft_policy` ships for a record: its flow dump, stripped."""
    return yaml.safe_dump(value, **YAML_DUMP_ARGUMENTS).strip()


def _yaml_safe_dump(args: tuple, kwargs: dict) -> Finisher:
    if len(args) != 1 or kwargs != YAML_DUMP_ARGUMENTS:
        raise ValueError(
            f"the yaml stage checks only Drafter.draft_policy's dump, which a yaml.safe_dump call with "
            f"{len(args)} positional arguments and the keywords {kwargs!r} is not"
        )
    record = args[0]

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, str)
        digest = hashlib.sha1(compact(encode(record)).encode("ascii")).hexdigest()[:20]
        return {"key": f"yaml:{digest}", "input": record, "value": result.strip()}

    return finish


# How long the generated set's long scalars are: past the dump's width, so the column checks that would break a line fire.
YAML_LONG_SCALAR = 10**6 + 12


def _yaml_edge_strings() -> list[str]:
    """The strings the `yaml` stage's generated set puts to the dump: every implicit resolver's near misses and hits, each indicator at the start, inside and at the end, leading and trailing whitespace, the characters PyYAML escapes or writes as they are, and line breaks in every position."""
    resolvers = [
        *"yes Yes YES yES no No NO true True TRUE tRUE false False FALSE on On ON off Off OFF y n Y N".split(),
        *"nul null Null NULL nULL ~ ~~ < << <<< = == ! & * !a".split(),
        *"0 -0 +0 00 07 08 0o7 0b1 0b -0b1_0 0b2 0x 0x1F +0x_f 0xg 0X1F 1_000 +12 -12 _1 1_".split(),
        *"1:30 1:60 1:5 190:20:30 1:3: 012:30 -1:30 1:30.5 1:3.".split(),
        *"1. 1.5 -1.5e+3 1.5e3 1e5 1.5e+ .5 .5E-1 . ._ .inf -.Inf +.INF .iNf .nan .NaN -.nan 1_0.0_1".split(),
        "2001-12-14",
        "2001-12-1",
        "2001-12-14t21:59:43.10-05:00",
        "2001-12-14 21:59:43.10 -5",
        "2001-1-1T1:00:00",
        "2001-12-14\t 1:59:43Z",
        "2001-12-14 21:59:43.Z",
        "2001-12-14 21:59:43 +05:3",
        "2001-12-14 21:59:43+05:30",
        "2001-12-14T21:59",
        "",
        "true\n",
        "null\n",
        "5\n",
        "5\n\n",
    ]
    indicators = [
        *"-a - -- --- ---a ... .... ...a ?a ? :a : a:b a: a?b a,b ,a a] [a {a a} a#b #a &a *a !a |a >a %a @a `a".split(),
        "- a",
        "--- a",
        "? a",
        ": a",
        "a: b",
        "a ?",
        "a #b",
        "a# b",
        "'a",
        '"a',
        "a'b",
        'a"b',
        "a b",
        "a  b",
        " a",
        "a ",
        "a　",
        "　a",
        "·Pea ~b~ ·Tea | ·Day+Utter",
        "E650:E652",
        "Reviewer rejected the M1 outcome for E650:E652 (·Pea ~b~ ·Tea): note",
    ]
    characters = [
        "a\tb",
        "a\rb",
        "".join(chr(point) for point in range(0x100)),
        *(
            chr(point)
            for point in (
                *range(0x20),
                *range(0x7F, 0xA2),
                0xAD,
                0x2028,
                0x2029,
                0xD7FF,
                0xE000,
                0xE650,
                0xFEFF,
                0xFFFD,
                0xFFFE,
                0xFFFF,
                0x10000,
                0x1F600,
                0x10FFFE,
                0x10FFFF,
            )
        ),
        "a\U0010ffffb",
        "\U0001f600 a\xa0b﻿c",
    ]
    breaks = [
        "a\nb",
        "a\n\nb",
        "\na",
        "a\n",
        "\n",
        "\n\n",
        "a \nb",
        "a\n b",
        "a\n\n b",
        "a\r\nb",
        "a\x85b",
        "a b",
        "a  b",
        "a\n\x85b",
        "\x85a",
        "a\x85",
        "it's\nok",
        "'\n'",
        "a\n\tb",
        "a b\nc d\n\ne f",
    ]
    quotes = ["it's", "'", "''", 'say "hi"', "'\"", "\\", "a\\b", "\\'"]
    return resolvers + indicators + characters + breaks + quotes


def _yaml_edges() -> list[object]:
    """The values the `yaml` stage's generated lines dump, each built fresh, since PyYAML anchors a container it meets twice: every edge string at the root, as a mapping value, as a mapping key and two levels deep in a flow sequence; ints, booleans and None in the same places; empty and nested containers and keys that are containers; keys at either side of the simple-key length limit; scalars long enough to pass the width; and records shaped like the drafter's."""
    values: list[object] = []
    scalars: list[object] = [*_yaml_edge_strings(), 0, 7, -1, 2**63 - 1, -(2**63), True, False, None]
    for scalar in scalars:
        values += [scalar, {"v": scalar}, {scalar: 1}, {"a": [{"b": scalar}, scalar]}]
    values += [
        [],
        {},
        (),
        [[]],
        [{}],
        ([], {}),
        {"a": []},
        {"a": {}},
        [1, [2, (3,)]],
        {(): 1},
        {(1, "a"): 2},
        {(1, ()): 3},
        {((),): 4},
        {None: 1, True: 2, 5: 3, -5: 4, "": 5},
        {"x" * 122: 1},
        {"x" * 123: 1},
        {"\xe9" * 122: 1},
        {"\xe9" * 123: 1},
        {"a" * 60 + ": " + "b" * 60: 1},
        {"exit": "y5", "when": {"right": {"family": ["qsTea"]}}, "why": "Reviewer rejected"},
        {"entry": "y0", "by": 1, "when": {"left": {"family": ["qsPea"]}}, "why": "a: b"},
        {"cell": {"exit": "none"}, "over": {"entry": "y5"}, "mode": "absolute", "when": {"word": "isolated"}},
        {"stance": "half", "mode": "absolute", "when": {"word": "isolated"}, "why": "it's"},
    ]
    long_plain = ("ab " * (YAML_LONG_SCALAR // 3 + 1)).strip()
    long_single = "a: " * (YAML_LONG_SCALAR // 3 + 1)
    long_double = "\tb " * (YAML_LONG_SCALAR // 3 + 1)
    long_breaks = "ab \n" * (YAML_LONG_SCALAR // 4 + 1)
    for long in (long_plain, long_single, long_double, long_breaks):
        values += [long, [long, "x", "y"], {"k": long, "j": 1}]
    return values


def _yaml_generated(_source: Source) -> Iterator[Mapping[str, object]]:
    for index, value in enumerate(_yaml_edges()):
        yield {"key": f"yaml:edge:{index}", "input": value, "value": yaml_dump(value)}


def _shaped_font(shaper: object) -> str:
    """The resolved path of the font a worker shaper shapes with. Every shaper the worker shapes with comes from `ink.shaper_for`, whose registry keys it by that path; any other shaper is refused."""
    for (path, _mtime, _size), registered in ink._shaper_registry.items():
        if registered is shaper:
            return path
    raise ValueError("the shape stage records only shapers ink.shaper_for hands out")


def _shape_line(font: str, text: str, features: object, result: object) -> dict[str, object]:
    """One `shape` line: the call, keyed by a digest of its typed encoding, and the `ShapeResult` it returned."""
    call = {"font": font, "text": text, "features": features}
    digest = hashlib.sha1(compact(encode(call)).encode("ascii")).hexdigest()[:20]
    return {"key": f"shape:{digest}", **call, "value": result}


def _shape_call(args: tuple, kwargs: dict) -> Finisher:
    shaper, text = args[0], args[1]
    features = args[2] if len(args) > 2 else kwargs.get("features")
    font = _shaped_font(shaper)
    return lambda result: _shape_line(font, text, features, result)


# The reprs of the diffs each `_ink_readings` call returns while a `_phase1_unit` call runs, which that call's `ink` line takes.
_INK_DIFFS: list[str] = []


def _ink_diffs(_args: tuple, _kwargs: dict) -> Finisher:
    def finish(result: object) -> None:
        assert isinstance(result, tuple)
        _INK_DIFFS.append(repr(result[0]))

    return finish


def _ink_unit(args: tuple, _kwargs: dict) -> Finisher:
    unit = args[0]
    key = f"ink:{unit.ordinal}"
    text = "".join(chr(value) for value in unit.codepoint_values)
    configs = unit.configs
    mark = len(_INK_DIFFS)

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, tuple)
        projection = result[0]
        (diffs,) = _INK_DIFFS[mark:]
        del _INK_DIFFS[mark:]
        return {
            "key": key,
            "text": text,
            "configs": configs,
            "value": {
                "diffs": diffs,
                "ink_identical": projection.ink_identical,
                "picture_identical": projection.picture_identical,
                "junior_equivalent": projection.junior_equivalent,
                "ink_deltas": projection.ink_deltas,
                "diffs_digest": projection.diffs_digest,
            },
        }

    return finish


def _ink_generated(source: Source) -> Iterator[Mapping[str, object]]:
    """The `ink` stage's generated lines: every distinct text of a source unit that holds `ss10`, in the handouts' order, put to the worker's Junior oracle directly, with its tracking-free Junior pieces and its answer for `ss10` alone. Phase 1 asks the oracle only about a unit under `ss10` alone that is neither ink- nor picture-identical, which the live corpus does not hold."""
    init = source.init
    oracle = ink.JuniorOracle(init["junior_font"], init["before_font"], init["after_font"], ink.shaper_for)
    texts = dict.fromkeys(
        "".join(chr(value) for value in unit.codepoint_values)
        for handout in source.handouts
        for unit in source.units(handout)
        if SS10 in unit.configs
    )
    try:
        for text in texts:
            digest = hashlib.sha1(compact(encode(text)).encode("ascii")).hexdigest()[:20]
            yield {
                "key": f"junior:{digest}",
                "text": text,
                "value": {
                    "junior_pieces": oracle._comparator.junior_pieces(text, oracle.tracking),
                    "approves": oracle.approves((SS10,), text),
                },
            }
    finally:
        ink.release_shape_memos()


def _settle_units(args: tuple, kwargs: dict) -> Finisher:
    units = tuple(args[1] if len(args) > 1 else kwargs["units"])

    def finish(result: object) -> list[Mapping[str, object]]:
        assert isinstance(result, list) and len(result) == len(units)
        return [
            {
                "key": f"settle:{unit.configs[0]}:{unit.codepoints}",
                "codepoints": unit.codepoint_values,
                "config": unit.configs[0],
                "value": Record(
                    "ExplainReport",
                    (
                        ("codepoints", report.codepoints),
                        ("features", report.features),
                        ("positions", report.positions),
                    ),
                ),
            }
            for unit, report in zip(units, result)
        ]

    return finish


def _settle_spans(args: tuple, kwargs: dict) -> Finisher:
    values = tuple(args[1] if len(args) > 1 else kwargs["codepoint_values"])
    key = "spans:" + ":".join(f"{value:04X}" for value in values)
    return lambda result: {"key": key, "codepoints": values, "value": result}


# The report and the text of the last `ExplainReport.render` call, which the `_filter_explain` call that follows it in `Enricher.enrich` receives.
_RENDERED: list[tuple[ExplainReport, str]] = []


def _explain_render(args: tuple, _kwargs: dict) -> Finisher:
    report = args[0]

    def finish(result: object) -> None:
        assert isinstance(result, str)
        _RENDERED[:] = [(report, result)]

    return finish


def _explain_filter(args: tuple, kwargs: dict) -> Finisher:
    rendered = args[0] if args else kwargs["rendered"]
    diff_positions = tuple(args[1] if len(args) > 1 else kwargs["diff_positions"])
    if not _RENDERED or _RENDERED[0][1] is not rendered:
        raise ValueError(
            "_filter_explain received a text that no recorded ExplainReport.render call returned"
        )
    report, _text = _RENDERED.pop()
    config = feature_config_token(report.features)
    codepoints = ":".join(f"{value:04X}" for value in report.codepoints)
    key = f"explain:{config}:{codepoints}:" + ",".join(str(index) for index in diff_positions)
    return lambda result: {
        "key": key,
        "codepoints": tuple(report.codepoints),
        "config": config,
        "diff_positions": diff_positions,
        "rendered": rendered,
        "filtered": result,
    }


def _subset_line(origin: str, config: str, codepoints: str, row: object) -> dict[str, object]:
    return {
        "key": f"{origin}:{config}:{codepoints}",
        "config": config,
        "codepoints": codepoints,
        "value": row,
    }


def _subset_row(args: tuple, kwargs: dict) -> Finisher:
    config = args[1] if len(args) > 1 else kwargs["config"]
    codepoints = args[2] if len(args) > 2 else kwargs["codepoints"]
    return lambda result: _subset_line("subset", config, codepoints, result)


# Spellings of a canonical window that `int(part, 16)` reads as the window itself: lowercase, the `0x` prefix bare and with its underscore, a sign, an underscore between digits, surrounding whitespace `int` strips (an em space and a next-line among it), a leading zero slot, and the decimal digits of two other scripts.
_SUBSET_SPELLINGS: tuple[Callable[[str], str], ...] = (
    str.lower,
    lambda window: ":".join(f"0x{part}" for part in window.split(":")),
    lambda window: ":".join(f"0X_{part}" for part in window.split(":")),
    lambda window: ":".join(f"+{part}" for part in window.split(":")),
    lambda window: ":".join(f"{part[:2]}_{part[2:]}" for part in window.split(":")),
    lambda window: ":".join(f" \t{part} \x85" for part in window.split(":")),
    lambda window: f"0000:{window}" if window.count(":") < 3 else window,
    lambda window: window.translate({ord(digit): 0x0660 + int(digit) for digit in "0123456789"}),
    lambda window: window.translate({ord(digit): 0xFF10 + int(digit) for digit in "0123456789"}),
)

# Misspellings of a canonical window that `pack_key` refuses, each of which a reader too lenient by one rule would read as the window: what `int(part, 16)` refuses (a leading, trailing or doubled underscore, a doubled underscore after the prefix, a sign twice, inner whitespace, an ASCII separator `str.isspace` accepts but `int` does not strip, a NUL, a DEL), a negative, a codepoint past the slot width and one past 64 bits that both end in the window's first codepoint, zero slots that fill the key past four, and empty parts.
_SUBSET_MISSPELLINGS: tuple[Callable[[str], str], ...] = (
    lambda window: f"_{window}",
    lambda window: f"{window}_",
    lambda window: f"{window[:2]}__{window[2:]}",
    lambda window: f"0x__{window}",
    lambda window: f"+-{window}",
    lambda window: f"{window[:2]} {window[2:]}",
    lambda window: f"\x1c{window}",
    lambda window: f"{window}\x00",
    lambda window: f"{window}\x7f",
    lambda window: f"-{window}",
    lambda window: f"1{window}",
    lambda window: f"1{'0' * 16}{window}",
    lambda window: "0000:" * (5 - len(window.split(":"))) + window,
    lambda window: f":{window}",
    lambda window: f"{window}:",
    lambda window: window.replace(":", "::", 1),
)

# Strings `pack_key` refuses or no table holds: nothing, a sign alone, a bare prefix, a letter outside hex, a negative zero and a zero key, which pack, and a codepoint past the slot width.
_SUBSET_MISSES = ("", "+", "0x", "G650", "-0", "0000", "10000")


def _subset_generated(source: Source) -> Iterator[Mapping[str, object]]:
    """The `subset` stage's generated lines (the module docstring), each looked up in the source's pack as `Enricher.subset_row` opens it."""
    init = source.init
    pack = SubsetPack.open(init["subset_pack"], table_digests(init["subset_dir"], ACCEPTANCE_CONFIGS))
    try:
        windows = [
            (config, key_codepoints(section.keys[index]))
            for config, section in pack._sections.items()
            if section.rows
            for index in sorted({0, section.rows - 1})
        ]
        config, window = windows[0]
        lookups = [
            *windows,
            *((config, spell(window)) for spell in _SUBSET_SPELLINGS),
            *((config, misspell(window)) for misspell in _SUBSET_MISSPELLINGS),
            *((config, text) for text in _SUBSET_MISSES),
            ("ss99", window),
        ]
        for config, codepoints in lookups:
            yield _subset_line("generated", config, codepoints, pack.row(config, codepoints))
    finally:
        pack.close()


# The `EnrichedUnit` fields the `enrich` stage records, every one but the unit, which is the line's input.
ENRICH_FIELDS = tuple(item.name for item in dataclasses.fields(EnrichedUnit) if item.name != "unit")

# The `enrich` stage's outputs: the recorded fields, the mismatch lines, and the unit's primary-unit projection.
ENRICH_OUTPUTS = (*ENRICH_FIELDS, "mismatches", "primary_unit_projection")

# The `enrich` stage's `geometry` fields: what `Enricher.enrich` computes before its summary.
ENRICH_GEOMETRY = (
    "before_glyphs",
    "before_junctions",
    "after_cells",
    "after_junctions",
    "after_extensions",
    "diff_positions",
    "pair",
    "highlight_before",
    "highlight_after",
    "boundary_marks",
    "after_spans",
    "before_spans",
    "secondary_junctions",
    "pair_codepoints",
)


def _enrich_line(key: str, enricher: Enricher, unit: Unit) -> Callable[[object], dict[str, object]]:
    """The `enrich` line of one `Enricher.enrich` call about to run on `unit`: its inputs now, and a finisher that adds the call's outputs from its result and the mismatch lines the call adds to the enricher's list."""
    mark = len(enricher.mismatches)
    line: dict[str, object] = {
        "key": key,
        "record": compact(units_exec.unit_record(unit)),
        "flags": {
            "ink_identical": unit.ink_identical,
            "picture_identical": unit.picture_identical,
            "junior_equivalent": unit.junior_equivalent,
        },
    }

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, EnrichedUnit)
        outputs: dict[str, object] = {name: getattr(result, name) for name in ENRICH_FIELDS}
        return {
            **line,
            **outputs,
            "mismatches": tuple(enricher.mismatches[mark:]),
            "primary_unit_projection": primary_unit_projection(result),
        }

    return finish


def _enrich_unit(args: tuple, kwargs: dict) -> Finisher:
    enricher = args[0]
    unit = args[1] if len(args) > 1 else kwargs["unit"]
    return _enrich_line(f"enrich:{unit.ordinal}", enricher, unit)


# The suffix the `enrich` stage's generated rows give their last glyph name, so the before font's kern-neutral shaping disagrees with the row.
RENAMED_GLYPH_SUFFIX = ".renamed"


def _enrich_generated(source: Source) -> Iterator[Mapping[str, object]]:
    """The `enrich` stage's generated lines, which make both of `Enricher.enrich`'s mismatch lines fire, as the live corpus does not. The first unit of each first configuration in the handouts' order goes through `build._phase1_unit` as the worker runs it, so it carries the phase-1 flags, and is then enriched twice against that run's report: as a copy whose first `new` token is replaced by another cell token, under `generated:new:<ordinal>`, when its `new` holds only cell tokens; and against its subset row with the last glyph renamed (`RENAMED_GLYPH_SUFFIX`), which the line carries as its `row`, under `generated:row:<ordinal>`."""
    init = source.init
    firsts: dict[str, Unit] = {}
    for handout in source.handouts:
        for unit in source.units(handout):
            firsts.setdefault(unit.configs[0], unit)
    tokens = tuple(dict.fromkeys(token for unit in firsts.values() for token in unit.new if "/" in token))
    comparator = ink.InkComparator(init["before_font"], init["after_font"], ink.shaper_for)
    oracle = ink.JuniorOracle(init["junior_font"], init["before_font"], init["after_font"], ink.shaper_for)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(init["spec_root"])
    enricher = Enricher(
        spec,
        init["subset_dir"],
        init["after_font"],
        repo_root=init["repo_root"],
        before_font=init["before_font"],
        shaper_factory=ink.shaper_for,
        subset_pack=init["subset_pack"],
    )
    drafter = Drafter(
        init["after_font"],
        corpus_index=init["corpus_index"],
        repo_root=init["repo_root"],
        shaper_factory=ink.shaper_for,
    )
    try:
        for unit in firsts.values():
            (report,) = enricher.explain_units([unit])
            build._phase1_unit(
                unit, comparator, oracle, enricher, drafter, report, init["identical_classes"], init["fold"]
            )
            if unit.new and all("/" in token for token in unit.new):
                variant = copy.copy(unit)
                variant.new = (next(token for token in tokens if token != unit.new[0]), *unit.new[1:])
                finish = _enrich_line(f"generated:new:{unit.ordinal}", enricher, variant)
                yield finish(enricher.enrich(variant, report))
            row = enricher.subset_row(unit.configs[0], unit.codepoints)
            assert row is not None
            renamed = dataclasses.replace(
                row, glyphs=(*row.glyphs[:-1], row.glyphs[-1] + RENAMED_GLYPH_SUFFIX)
            )
            finish = _enrich_line(f"generated:row:{unit.ordinal}", enricher, unit)
            setattr(enricher, "subset_row", lambda _config, _codepoints: renamed)
            try:
                result = enricher.enrich(unit, report)
            finally:
                delattr(enricher, "subset_row")
            yield {**finish(result), "row": renamed}
    finally:
        ink.release_shape_memos()


# The scaffolds the `unit_scaffold` calls inside the `_phase1_unit` call under way returned, each copied before `unit_to_json` gives it the final class.
_SCAFFOLDS: list[object] = []


def _scaffold_returned(_args: tuple, _kwargs: dict) -> Finisher:
    def finish(result: object) -> None:
        _SCAFFOLDS.append(copy.deepcopy(result))

    return finish


def _scaffold_unit(args: tuple, _kwargs: dict) -> Finisher:
    unit = args[0]
    key = f"scaffold:{unit.ordinal}"
    record = compact(units_exec.unit_record(unit))
    mark = len(_SCAFFOLDS)

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, tuple)
        projection = result[0]
        (scaffold,) = _SCAFFOLDS[mark:]
        del _SCAFFOLDS[mark:]
        return {
            "key": key,
            "record": record,
            "ink_deltas": dict(projection.ink_deltas),
            "final_class": projection.change_group or None,
            "value": {
                "scaffold": scaffold,
                "change_group": projection.change_group,
                "cluster": projection.cluster,
            },
        }

    return finish


def _scaffold_generated(_source: Source) -> Iterator[Mapping[str, object]]:
    """The `scaffold` stage's generated lines: every non-empty subset of `ACCEPTANCE_CONFIGS`, its members in config order, put to `build.config_badge` against `ACCEPTANCE_CONFIGS`, under `badge:` and the subset. The corpus's config sets reach neither the three-clause gates nor the `only under:` fallback."""
    for size in range(1, len(ACCEPTANCE_CONFIGS) + 1):
        for configs in combinations(ACCEPTANCE_CONFIGS, size):
            gate, note = build.config_badge(configs, ACCEPTANCE_CONFIGS)
            yield {
                "key": "badge:" + ",".join(configs),
                "configs": configs,
                "value": {"gate": gate, "note": note},
            }


def _digest_key(prefix: str, call: Mapping[str, object]) -> str:
    return prefix + hashlib.sha1(compact(encode(call)).encode("ascii")).hexdigest()[:20]


def _raising(call: Callable[[], object]) -> object:
    """What `call` returns, or, when it raises ValueError or AssertionError, the exception's class name and message, which is what the replay gives a Python caller."""
    try:
        return call()
    except (ValueError, AssertionError) as error:
        return {"raised": type(error).__name__, "message": str(error)}


def _expect_line(
    drafter: Drafter, text: str, expect: str, features: dict[str, bool] | None, verdict: object
) -> dict[str, object]:
    """One `pin:` line of the `expect` stage: the replay's inputs, keyed by a digest of them, and as its value the pin's `parse` (its tokens and connections, or the ValueError's message), the `interpretations` `_expand_maybe_ligatures` expands it to, the `row` `row_for` shapes the text into with the drafter's after font under the replay's kern-neutral features, and the `verdict`, what `Drafter.validate_semantics` returned or raised (`_raising`). The interpretations and the row are None for a pin that does not parse, for which the replay shapes nothing."""
    call = {"text": text, "expect": expect, "features": features}
    ts = drafts._import_test_shaping()
    try:
        tokens, connections = ts.parse_expect(expect)
    except ValueError as error:
        parse, interpretations, row = str(error), None, None
    else:
        parse = (tokens, connections)
        interpretations = ts._expand_maybe_ligatures(list(tokens), list(connections))
        row = _raising(
            lambda: row_for(drafter.after_shaper, drafter.after_classifier, text, ink.kern_neutral(features))
        )
    return {
        "key": _digest_key("pin:", call),
        **call,
        "value": {"parse": parse, "interpretations": interpretations, "row": row, "verdict": verdict},
    }


def _expect_replay(args: tuple, kwargs: dict) -> Finisher:
    drafter, text, expect = args[0], args[1], args[2]
    features = args[3] if len(args) > 3 else kwargs.get("features")
    return lambda result: _expect_line(drafter, text, expect, features, result)


def _expect_any_of(args: tuple, kwargs: dict) -> Finisher:
    drafter = args[0]
    unit = (args[1] if len(args) > 1 else kwargs["enriched"]).unit
    text = "".join(chr(value) for value in unit.codepoint_values)
    features = drafts.features_dict(unit.configs) or None

    def finish(result: object) -> list[Mapping[str, object]]:
        assert isinstance(result, drafts.AnyOfDraft)
        return [
            _expect_line(
                drafter,
                text,
                candidate,
                features,
                _raising(lambda candidate=candidate: drafter.validate_semantics(text, candidate, features)),
            )
            for candidate in result.candidates
        ]

    return finish


def _expect_string_line(
    values: object, spans: object, junctions: object, result: object
) -> dict[str, object]:
    call = {"codepoints": values, "spans": spans, "junctions": junctions}
    return {"key": _digest_key("string:", call), **call, "value": result}


def _expect_string(args: tuple, kwargs: dict) -> Finisher:
    values = args[0] if args else kwargs["codepoint_values"]
    spans = args[1] if len(args) > 1 else kwargs["spans"]
    junctions = args[2] if len(args) > 2 else kwargs["junctions"]
    return lambda result: _expect_string_line(values, spans, junctions, result)


# The `expect` stage's generated `drafts.expect_string` inputs: every boundary token, every connector, the two letters whose display is not their name, and a three-letter span.
_EXPECT_STRINGS: tuple[tuple[tuple[int, ...], tuple[tuple[int, int], ...], tuple[str, ...]], ...] = (
    ((0x200C, 0xE652, 0xE679), ((0, 1), (1, 3)), ("break",)),
    (
        (0x00B7, 0xE650, 0x0020, 0xE664, 0xE65D),
        ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5)),
        ("break", "y0", "y5", "y6"),
    ),
    ((0xE650, 0xE652, 0xE67A, 0xE653), ((0, 1), (1, 3), (3, 4)), ("y8", "break")),
    ((0xE652, 0xE67A, 0xE679), ((0, 3),), ()),
)

_PEA, _TEA, _DAY, _IT, _UTTER, _ZWNJ, _VS1 = "", "", "", "", "", "‌", "︀"


def _expect_edges() -> list[tuple[str, str, dict[str, bool] | None]]:
    """The `expect` stage's generated pins, each a text, an expect string and the replay's features: every connector, ligature mode, variant assertion, escape and lozenge, every parse error with texts its message must quote (quotes, an unprintable character, a code point beyond the BMP, the whitespace `str.isspace` accepts and Rust's `char::is_whitespace` does not), and the replays the corpus does not reach (more than one maybe-ligature, variation selectors, ◊ZWNJ, `ss10`, texts too short or too long for the tokens, and an empty text)."""
    word = _PEA + _TEA
    pins = [
        "",
        "   ",
        "·Pea ·Tea",
        "·Pea~x~·Tea",
        "·Pea ~b~ ·Tea",
        "·Pea ~t~ ·Tea",
        "·Pea ~6~ ·Tea",
        "·Pea | ·Tea",
        "·Pea |?| ·Tea",
        "·Pea ? ·Tea",
        "·Pea?·Tea",
        "·Pea |? ·Tea",
        "·Pea.half ·Tea.!half",
        "·Pea.alt.!half.extended ·Tea.half.∅",
        "·Pea.∅ ·Tea.∅",
        "·Pea.∅.∅ ·Tea",
        "·Pea.∅.half ·Tea",
        "·Pea.Half ·Tea",
        "·Pea.!Half ·Tea",
        "·Pea. ·Tea",
        "·pea ·Tea",
        "Pea ·Tea",
        "| ·Pea ·Tea",
        "·Pea ·Tea |",
        "·Pea ·Tea ~x~",
        "·Pea ~y~ ·Tea",
        "·Pea ·Tea",
        "·Pea　~x~ ·Tea",
        "·Pea\x1c·Tea",
        "\x85·Pea ·Tea\x85",
        "·Pea\t|\t·Tea",
        "·Pea​·Tea",
        "·Pea 'x'",
        '·Pea "x\'"',
        "·Pea ͸\x7f\U0001f600",
        "·Pea+Tea",
        "·Pea+tea",
        "·Pea+Tea+Day",
        "·Pea+??Tea",
        "\\· ·Pea",
        "\\x ·Tea",
        f"\\{_PEA} ·Tea",
        "\\",
        "\\\n ·Tea",
        "\\\U0001f600 ·Tea",
        "◊space ·Tea",
        "◊ZWNJ ·Tea",
        "◊zwnj ·Tea",
        "◊ ·Tea",
        "◊space1 ·Tea",
        "·-ing ·J'ai",
        "·J’ai ·Jai",
        "·Jay ·J'x",
        "·Jaix ·Tea",
        "·Tea+J’ai",
    ]
    edges: list[tuple[str, str, dict[str, bool] | None]] = [(word, pin, None) for pin in pins]
    edges += [
        ("", "", None),
        ("", "·Pea", None),
        (_PEA, "", None),
        (_PEA, "·Pea ·Tea", None),
        (_PEA, "·Tea", None),
        (_TEA, "·Tea+Utter", None),
        (_TEA + _UTTER, "·Tea+Utter", None),
        (_TEA + _UTTER, "·Tea+Utter.∅", None),
        (_TEA + _UTTER, "·Tea+?Utter", None),
        (_TEA + _UTTER, "·Tea+|Utter", None),
        (_TEA + _UTTER + _DAY + _UTTER, "·Tea+?Utter ·Day+|Utter", None),
        (_TEA + _UTTER + _DAY + _UTTER, "·Tea+?Utter ~x~ ·Day+?Utter", None),
        (_TEA + _UTTER + _DAY + _UTTER, "·Tea ·Utter ·Day ·Utter", None),
        (_PEA + _IT + _TEA, "·Pea ~x~ ·It.half | ·Tea.alt", None),
        (_PEA + _VS1 + _TEA, "·Pea ·Tea", None),
        (_VS1 + _PEA + _TEA + _VS1, "·Pea | ·Tea", None),
        (_PEA + _VS1 + _VS1, "·Pea", None),
        (_PEA + _ZWNJ + _TEA, "·Pea | ◊ZWNJ | ·Tea", None),
        (_PEA + _ZWNJ + _TEA, "·Pea ◊ZWNJ ·Tea", None),
        (_PEA + " " + _TEA, "·Pea | ◊space | ·Tea", None),
        (_PEA + "·" + _TEA, "·Pea | \\· | ·Tea", None),
        (word, "·Pea | ·Tea", {"ss10": True}),
        (word, "·Pea ·Tea", {"ss10": True}),
        (word, "·Pea ·Tea", {"ss03": True, "ss05": True}),
        (word, "·Pea ·Tea", {"kern": True}),
        (word, "·Pea ·Tea", {}),
        ("ab", "\\a | \\b", None),
        ("a", "\\a \\b", None),
    ]
    return edges


def _expect_generated(source: Source) -> Iterator[Mapping[str, object]]:
    """The `expect` stage's generated lines: `drafts.expect_string` over `_EXPECT_STRINGS`, under `string:` and a digest of the call; and the replay of every pinned cell of the site pages, its whole text against its whole expect string under the cell's stylistic sets, and of `_expect_edges`, each under `pin:` and a digest of the replay's inputs (`_expect_line`), through a drafter over the source's after font."""
    init = source.init
    drafter = Drafter(
        init["after_font"],
        corpus_index=init["corpus_index"],
        repo_root=init["repo_root"],
        shaper_factory=ink.shaper_for,
    )
    ts = drafts._import_test_shaping()

    def line(text: str, expect: str, features: dict[str, bool] | None) -> dict[str, object]:
        verdict = _raising(lambda: drafter.validate_semantics(text, expect, features))
        return _expect_line(drafter, text, expect, features, verdict)

    try:
        for values, spans, junctions in _EXPECT_STRINGS:
            yield _expect_string_line(
                values, spans, junctions, drafts.expect_string(values, spans, junctions)
            )
        for page in fingerprint.read_site_corpus(REPO_ROOT).values():
            if page is None:
                continue
            collector = ts._DataExpectCollector()
            collector.feed(page.decode("utf-8"))
            for text, expect, _line, stylistic_set, _runs in collector.cells:
                if expect is None:
                    continue
                features = (
                    {f"ss{ss.zfill(2)}": True for ss in stylistic_set.split()} if stylistic_set else None
                )
                yield line(text, expect, features)
        for text, expect, features in _expect_edges():
            yield line(text, expect, features)
    finally:
        ink.release_shape_memos()


# The definitions `Drafter.__init__` builds its checkers for, each of which the `schema` stage's generated set checks every base record against.
SCHEMA_DEFINITIONS = ("refuseRecord", "preferRecord", "contractRecord")
# The definition each policy list of a rune file is checked against in the `schema` stage's generated set: a drafted contract record has an extend record's shape.
SCHEMA_RUNE_LISTS = (
    ("refuse", "refuseRecord"),
    ("prefer", "preferRecord"),
    ("contract", "contractRecord"),
    ("extend", "contractRecord"),
)
# What the `schema` stage's mutations put in place of a value: every JSON kind, the Python kinds the schema must not take for one (a tuple, bytes, a frozenset), a dict with a key that is not a str, and strings near the schema's patterns and enums.
SCHEMA_PROBES: tuple[object, ...] = (
    None,
    True,
    False,
    0,
    1,
    -1,
    2,
    "",
    "x",
    "\t",
    "baseline",
    "none",
    "absolute",
    "half",
    "a-b",
    "ss01",
    "qsPea",
    "qsPea\n",
    "qsPea_qsTea",
    "isolated",
    b"x",
    frozenset({"x"}),
    [],
    ["qsPea"],
    ("qsPea",),
    {},
    {"family": "qsPea"},
    {1: "x"},
)
# The keys the `schema` stage's mutations add to each dict that lacks them, each with a value of the shape its definition takes, and two the schema knows nowhere.
SCHEMA_ADDITIONS: dict[object, object] = {
    "id": "a-b",
    "stance": "half",
    "entry": "baseline",
    "exit": "top",
    "cell": {"exit": "none"},
    "over": {"entry": "y6"},
    "mode": "absolute",
    "by": 1,
    "trim": 2,
    "why": "w",
    "when": {"word": "isolated"},
    "family": ["qsPea"],
    "class": "c",
    "except": [{"family": "qsTea"}],
    "then": {"family": "qsTea"},
    "self": {"entry": "live"},
    "left": {"family": "qsPea"},
    "right": {"family": "qsPea"},
    "zz": 1,
    2: "x",
}
# One drafted record of each shape the `schema` stage's run checks, with its definition, by the shape (`_schema_shape`): the drafted bases its generated set mutates, filled by the run that precedes the generators in `dump`.
_SCHEMA_DRAFTED: dict[str, tuple[str, object]] = {}


def _schema_shape(definition: str, record: object) -> str:
    """A drafted record's definition and the record without its `why`, which names the unit and so differs on nearly every record."""
    if isinstance(record, dict):
        record = {key: value for key, value in record.items() if key != "why"}
    return compact(encode([definition, record]))


def _schema_line(origin: str, definition: str, record: object, problems: object) -> dict[str, object]:
    call = {"definition": definition, "record": record}
    assert isinstance(problems, list)
    return {"key": _digest_key(f"{origin}:", call), **call, "value": not problems}


def _schema_check(args: tuple, kwargs: dict) -> Finisher | None:
    checker = args[0]
    record = args[1] if len(args) > 1 else kwargs["value"]
    definition = next((name for name, chosen in checker._definitions.items() if chosen is checker), None)
    if definition is None:
        return None
    _SCHEMA_DRAFTED.setdefault(_schema_shape(definition, record), (definition, copy.deepcopy(record)))
    return lambda problems: _schema_line("drafted", definition, record, problems)


def _schema_variants(value: object) -> Iterator[object]:
    """What the `schema` stage's mutations put in place of `value`: the probes, and variants of the value itself, near misses of a pattern for a string and the wrong container or length for a list."""
    yield from SCHEMA_PROBES
    if isinstance(value, str):
        yield from (
            value + "\n",
            value + "\n\n",
            value + "x",
            "x" + value,
            " " + value,
            value.upper(),
            value[:-1],
        )
    elif isinstance(value, list):
        yield from (tuple(value), value + value[:1], value[1:], [value])
    elif isinstance(value, dict):
        yield [value]
    elif isinstance(value, int) and not isinstance(value, bool):
        yield from (value - 1, value + 1)


def _schema_mutations(record: object) -> Iterator[object]:
    """Every mutation of `record` the `schema` stage's generated set checks: at every place in it, the value replaced by each variant (`_schema_variants`), each dict entry and list item removed, and each addition (`SCHEMA_ADDITIONS`) a dict lacks added."""

    def at(value: object, rebuilt: Callable[[object], object]) -> Iterator[object]:
        for variant in _schema_variants(value):
            yield rebuilt(variant)
        if isinstance(value, dict):
            for key, child in value.items():
                yield rebuilt({other: item for other, item in value.items() if other != key})
                yield from at(child, lambda new, key=key: rebuilt({**value, key: new}))
            for key, addition in SCHEMA_ADDITIONS.items():
                if key not in value:
                    yield rebuilt({**value, key: addition})
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield rebuilt(value[:index] + value[index + 1 :])
                yield from at(
                    item, lambda new, index=index: rebuilt([*value[:index], new, *value[index + 1 :]])
                )

    yield from at(record, lambda new: new)


def _schema_generated(source: Source) -> Iterator[Mapping[str, object]]:
    """The `schema` stage's generated lines, each checked by the rune schema the source's worker reads, under `generated:` and a digest of the definition and the record: the bases, one drafted record of each shape the run checked (`_SCHEMA_DRAFTED`) and every policy record of the source's rune files (`SCHEMA_RUNE_LISTS`), each against every definition the drafter checks (`SCHEMA_DEFINITIONS`), and each base's mutations (`_schema_mutations`) against its own definition."""
    schema_file = Path(source.init["repo_root"]) / "rebuild" / "schema" / "rune.schema.json"
    schema = spec_load._load_schema(schema_file.parent, schema_file.name)
    bases = list(_SCHEMA_DRAFTED.values())
    for path in sorted((Path(source.init["spec_root"]) / "glyph_data" / "runes").glob("*.yaml")):
        policy = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("policy") or {}
        for name, definition in SCHEMA_RUNE_LISTS:
            bases += [(definition, record) for record in policy.get(name) or ()]

    def line(definition: str, record: object) -> dict[str, object]:
        return _schema_line("generated", definition, record, schema.for_definition(definition).check(record))

    for definition, record in bases:
        for other in SCHEMA_DEFINITIONS:
            yield line(other, record)
        for mutated in _schema_mutations(record):
            yield line(definition, mutated)


def _draft_unit(args: tuple, _kwargs: dict) -> Finisher:
    unit = args[0]
    key = f"draft:{unit.ordinal}"
    record = compact(units_exec.unit_record(unit))

    def finish(result: object) -> dict[str, object] | None:
        assert isinstance(result, tuple)
        projection, fragment = result[0], result[1]
        if "drafts" not in fragment:
            return None
        return {
            "key": key,
            "record": record,
            "flags": {
                "ink_identical": projection.ink_identical,
                "picture_identical": projection.picture_identical,
                "junior_equivalent": projection.junior_equivalent,
            },
            "value": fragment["drafts"],
        }

    return finish


# The shard bytes the `_fragment_bytes` calls inside the `_phase1_unit` call under way returned.
_FRAGMENT_BYTES: list[bytes] = []


def _fragment_bytes_returned(_args: tuple, _kwargs: dict) -> Finisher:
    def finish(result: object) -> None:
        assert isinstance(result, bytes)
        _FRAGMENT_BYTES.append(result)

    return finish


def _fragment_unit(args: tuple, _kwargs: dict) -> Finisher:
    unit = args[0]
    key = f"fragment:{unit.ordinal}"
    record = compact(units_exec.unit_record(unit))
    mark = len(_FRAGMENT_BYTES)

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, tuple)
        projection, fragment = result[0], result[1]
        (body,) = _FRAGMENT_BYTES[mark:]
        del _FRAGMENT_BYTES[mark:]
        return {
            "key": key,
            "record": record,
            "flags": {
                "ink_identical": projection.ink_identical,
                "picture_identical": projection.picture_identical,
                "junior_equivalent": projection.junior_equivalent,
            },
            "ink_deltas": dict(projection.ink_deltas),
            "final_class": projection.change_group or None,
            "value": {
                "fragment": body.decode("ascii"),
                "content_key": fragment["content_key"],
                "id": fragment["id"],
            },
        }

    return finish


def _fragment_packed(
    init: Mapping[str, object], expected: Mapping[str, object], packed: object
) -> str | None:
    """The `fragment` stage's answer check: the problem with the Rust answer's deflated fragment, or None when it inflates through the source's preset dictionary to the Python fragment's bytes and its stream ends exactly where its bytes end, the two things `build._FragmentArena.read` requires of a span."""
    if not isinstance(packed, bytes):
        return f"the deflated fragment is {type(packed).__name__}, not bytes"
    value = expected.get("value")
    if not isinstance(value, dict):
        return None
    dictionary = init["dictionary"]
    assert isinstance(dictionary, bytes)
    inflater = zlib.decompressobj(zdict=dictionary)
    try:
        body = inflater.decompress(packed)
    except zlib.error as error:
        return f"the deflated fragment does not inflate ({error})"
    if not inflater.eof or inflater.unused_data:
        return "the deflated fragment's stream does not end where its bytes end"
    if body != value["fragment"].encode("ascii"):
        return "the deflated fragment inflates to other bytes than the Python fragment's"
    return None


# The smallest fragment of each shape (`_check_shape`) the `check` stage's run checks, by shape, with its encoded length: the bases its generated set mutates, filled by the run that precedes the generators in `dump`.
_CHECK_DRAFTED: dict[str, tuple[int, dict]] = {}
# How many levels below each top-level fragment key the `check` stage's generic mutations reach; a key not named here is only replaced or removed.
CHECK_DEPTHS: dict[str, int] = {
    "before": 2,
    "after": 2,
    "highlight": 2,
    "pair": 1,
    "pair_codepoints": 1,
    "drafts": 3,
    "config_gate": 2,
    "render_groups": 2,
    "boundary_marks": 2,
    "ink_deltas": 1,
    "notation_tokens": 1,
    "configs": 1,
    "kinds": 1,
    "provenance": 1,
}
CHECK_PROBES: tuple[object, ...] = (
    None,
    True,
    False,
    0,
    1,
    -1,
    "",
    "x",
    "\n",
    [],
    ["x"],
    ("x",),
    {},
    {"x": 1},
)
# The exceptions `check_unit` raises on a malformed fragment, which the `check` stage records by class name.
CHECK_RAISES = (TypeError, AttributeError, ValueError)


def _check_shape(fragment: dict) -> str:
    """What sorts a drafted fragment into a base of the `check` stage's generated set: its slimness, its machine flags and exemption, and whether it carries a pair, a policy draft, a ledger class and a config gate."""
    drafted = fragment.get("drafts")
    return compact(
        encode(
            [
                audit.slim_fragment(fragment),
                [fragment.get(check_id) is True for check_id in audit.MACHINE_CHECKS],
                fragment.get("no_verdict") is True,
                fragment.get("pair") is None,
                not isinstance(drafted, dict) or drafted.get("policy") is None,
                bool(fragment.get("ledger_class")),
                fragment.get("config_gate") is None,
            ]
        )
    )


def _check_line(origin: str, fragment: dict) -> dict[str, object]:
    try:
        value: object = build.check_unit(fragment, at=(build.DRAFTED,))
    except CHECK_RAISES as error:
        value = {"raised": type(error).__name__}
    return {"key": _digest_key(f"{origin}:", {"fragment": fragment}), "fragment": fragment, "value": value}


def _check_unit(args: tuple, kwargs: dict) -> Finisher | None:
    if len(args) != 1 or kwargs != {"at": (build.DRAFTED,)}:
        return None
    fragment = args[0]
    text = compact(encode({"fragment": fragment}))
    key = "drafted:" + hashlib.sha1(text.encode("ascii")).hexdigest()[:20]
    shape = _check_shape(fragment)
    held = _CHECK_DRAFTED.get(shape)
    if held is None or len(text) < held[0]:
        _CHECK_DRAFTED[shape] = (len(text), copy.deepcopy(fragment))
    return lambda errors: {"key": key, "fragment": fragment, "value": errors}


def _check_variants(value: object) -> Iterator[object]:
    """What the `check` stage's generic mutations put in place of `value`: the probes, and variants of the value itself, near misses for a string, the wrong container, length or order for a list, a wrapped dict and a neighboring int."""
    yield from CHECK_PROBES
    if isinstance(value, str):
        yield from (value + "\n", value + "x", value[:-1], value.upper(), value.lower())
    elif isinstance(value, list):
        yield from (tuple(value), value + value[:1], value[1:], value[::-1], [value])
    elif isinstance(value, dict):
        yield [value]
    elif isinstance(value, int) and not isinstance(value, bool):
        yield from (value - 1, value + 1)


def _check_mutations(
    fragment: dict, depths: Mapping[str, int] = CHECK_DEPTHS, only: Collection[str] | None = None
) -> Iterator[dict]:
    """The `check` stage's generic mutations of `fragment`: each top-level entry removed, and at every place `depths` reaches below it, the value replaced by each variant (`_check_variants`), each dict entry removed, and a list's first and last item removed. With `only`, only the top-level entries it names are mutated."""

    def at(value: object, rebuilt: Callable[[object], dict], depth: int) -> Iterator[dict]:
        for variant in _check_variants(value):
            yield rebuilt(variant)
        if depth <= 0:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                yield rebuilt({other: item for other, item in value.items() if other != key})
                yield from at(child, lambda new, key=key: rebuilt({**value, key: new}), depth - 1)
        elif isinstance(value, list):
            for index in sorted({0, len(value) - 1}) if value else ():
                yield rebuilt(value[:index] + value[index + 1 :])
                yield from at(
                    value[index],
                    lambda new, index=index: rebuilt([*value[:index], new, *value[index + 1 :]]),
                    depth - 1,
                )

    for key, child in fragment.items():
        if only is not None and key not in only:
            continue
        yield {other: item for other, item in fragment.items() if other != key}
        yield from at(child, lambda new, key=key: {**fragment, key: new}, depths.get(key, 0))


def _check_extras(fragment: dict) -> Iterator[dict]:
    """The `check` stage's targeted mutations of `fragment`, one or more for each predicate the generic ones do not reach: ids and content keys `int(..., 16)` and the id pattern read in unusual ways, every combination of the machine flags and the exemption, mismatched deltas and ledger classes, config gates with their notes, render groups that do or do not partition the configs, iterables Python walks unexpectedly, junction tokens, spans, highlights, the slim keys on a slim fragment, and drafts whose members are malformed or whose any-of candidates do not parse."""

    def put(**changes: object) -> dict:
        return {**fragment, **changes}

    unit_id = fragment.get("id")
    if isinstance(unit_id, str):
        for spelling in (
            unit_id + "\n",
            unit_id + "\n\n",
            "\n" + unit_id,
            "e" + unit_id[1:],
            unit_id[:-1] + "0",
            unit_id[:-1] + "l",
            unit_id + "1",
            unit_id[:-1],
        ):
            yield put(id=spelling)
    stamp = fragment.get("content_key")
    if isinstance(stamp, str):
        for spelling in (
            stamp.upper(),
            stamp[:-1],
            stamp + "0",
            "g" + stamp[1:],
            "0x" + stamp[2:],
            "0X_" + stamp[3:],
            " " + stamp[1:],
            "　" + stamp[1:],
            "-" + stamp[1:],
            "+" + stamp[1:],
            stamp[:4] + "_" + stamp[5:],
            stamp[:4] + "__" + stamp[6:],
            "_" + stamp[1:],
            "٣" + stamp[1:],
            "１" + stamp[1:],
        ):
            yield put(content_key=spelling)
            try:
                matching = unit_cache.unit_id_for(spelling)
            except ValueError:
                continue
            yield put(content_key=spelling, id=matching)
    for flags in product((True, False), repeat=4):
        yield put(
            **dict(zip(("ink_identical", "picture_identical", "junior_equivalent", "no_verdict"), flags))
        )
    configs = fragment.get("configs")
    first = configs[0] if isinstance(configs, list) and configs else "default"
    for deltas in (
        {first: "d-" + "0" * 12},
        {first: "d-" + "A" * 12},
        {first: "d-" + "0" * 11},
        {first: "d-" + "0" * 13},
        {first: "e-" + "0" * 12},
        {first: "d-" + "٣" * 12},
        {"": "d-" + "0" * 12},
        {"elsewhere": "d-" + "0" * 12},
        {first: 5},
        {1: "d-" + "0" * 12},
        {},
    ):
        for ink_identical, picture_identical in ((True, False), (False, True), (False, False)):
            yield put(ink_deltas=deltas, ink_identical=ink_identical, picture_identical=picture_identical)
    for ledger in (fragment.get("class"), "other", "", 5):
        for no_verdict in (True, False):
            yield put(ledger_class=ledger, no_verdict=no_verdict)
    yield put(batch=1)
    for summary in ("Old: x", "New: a\nb", "New: ", "new: x", "New:x"):
        yield put(summary=summary)
    clause = {"feature": "ss03", "state": "on", "text": "with ss03 on"}
    other = {"feature": "ss05", "state": "off", "text": "and ss05 off"}
    for gate, note in (
        ([clause], "with ss03 on"),
        ([clause], "with ss03 on "),
        ([clause, other], "with ss03 on and ss05 off"),
        ([clause, other], "with ss03 on"),
        ([{**clause, "state": "maybe"}], "with ss03 on"),
        ([{**clause, "state": True}], "with ss03 on"),
        ([{**clause, "feature": 3}], "with ss03 on"),
        ([{**clause, "text": ""}], ""),
        ([{**clause, "text": 5}], "with ss03 on"),
        ([{"feature": "ss03", "state": "on"}], ""),
        ([clause, "with ss03 on"], "with ss03 on"),
        ([clause, None], "with ss03 on"),
        ([{**clause, "text": 5}, None], "with ss03 on"),
        ([[clause]], "with ss03 on"),
        ([], None),
        ((clause,), "with ss03 on"),
    ):
        yield put(config_gate=gate, config_note=note)
    listed = list(configs) if isinstance(configs, list) else []
    for groups in (
        [{"configs": listed}, {"configs": listed}],
        [{"configs": [*listed, "elsewhere"]}],
        [{"configs": [*listed, *listed[:1]]}],
        [{"configs": listed[::-1]}],
        [{"configs": [["x"]]}],
        [{"configs": [1, "a"]}],
        [{"configs": []}],
        [{"config": listed}],
        [{"configs": tuple(listed)}],
        ["configs"],
    ):
        yield put(render_groups=groups)
    for unit_configs, grouped in (
        (["b", "a"], ["a", "b"]),
        ([2, 1], [1, 2]),
        ([True, 2], [1, 2]),
        ([1, "a"], ["a", 1]),
        ([None, 1], [1, None]),
        ([("b",), ("a",)], [("a",), ("b",)]),
        ([("a", 1), ("a", "b")], [("a", 1), ("a", "b")]),
        ([["a"]], [["a"]]),
        (["a", "a"], ["a"]),
        (["a"], ["a", "a"]),
    ):
        yield put(configs=unit_configs, render_groups=[{"configs": grouped}], ink_deltas={})
    for marks in (
        "ab",
        {"index": 0},
        5,
        True,
        b"ab",
        [{"index": 0, "kind": "k"}],
        [{"index": 0, "kind": "k", "x": 1, "y": 2}],
        [["index", "kind", "x"]],
    ):
        yield put(boundary_marks=marks)
    for codepoints in ("e650", "", "E650::E651", "E650:G", ":", None, 5, "E650 "):
        yield put(codepoints=codepoints)
    for entities in ("&#xE650", "E650;", "&#x;", None):
        yield put(text_entities=entities)
    for side in ("before", "after"):
        record = fragment.get(side)
        if not isinstance(record, dict) or not isinstance(record.get("junctions"), list):
            continue
        junctions = record["junctions"]
        for token in ("y12", "y", "y٣", "y²", "y0+y5", "Y5", "absent", "lig", "break", "y-1", "y 5", 5, None):
            yield put(**{side: {**record, "junctions": [token] * max(len(junctions), 1)}})
        for name in ("glyphs", "cells", "extensions"):
            if name in record:
                yield put(**{side: {**record, name: "abc"}})
                yield put(**{side: {**record, name: {"a": 1}}})
    tokens = fragment.get("notation_tokens")
    count = len(tokens) if isinstance(tokens, list) else 2
    for pair in (
        {"left": 1, "right": 1},
        {"left": 2, "right": 1},
        {"left": True, "right": 2},
        {"left": "0", "right": 1},
        {"left": 0},
        None,
        [0, 1],
    ):
        yield put(pair=pair)
    for span in (
        [0, count],
        [0, count - 1],
        [True, True],
        [-1, 0],
        [1, 0],
        [0, 1, 2],
        (0, 1),
        None,
        [0, "1"],
    ):
        yield put(pair_codepoints=span)
        yield put(pair_codepoints=span, pair={"left": 0, "right": 1})
        yield put(pair_codepoints=span, pair=None)
    rect = {"x_min": 0, "x_max": 1, "advance_total": 2}
    for broken in (
        {"x_min": 5, "x_max": 1, "advance_total": 9},
        {"x_min": 0, "x_max": 9, "advance_total": 1},
        {"x_min": False, "x_max": True, "advance_total": 1},
        {"x_min": 0, "x_max": 1},
        {"x_min": 0, "x_max": 1, "advance_total": "2"},
        {"x_min": -1, "x_max": -1, "advance_total": -1},
    ):
        yield put(highlight={"before": rect, "after": broken})
        yield put(highlight={"before": broken, "after": rect})
    yield put(highlight=[rect, rect])
    for key, value in (
        ("highlight", {"before": rect, "after": rect}),
        ("explain", "x"),
        ("drafts", {"pin": None, "policy": None, "any_of": None}),
    ):
        if key not in fragment:
            yield put(**{key: value})
    drafted = fragment.get("drafts")
    if not isinstance(drafted, dict):
        return

    def draft(**changes: object) -> dict:
        assert isinstance(drafted, dict)
        return put(drafts={**drafted, **changes})

    pin = drafted.get("pin")
    for value in ("pin", ["pin"], 5):
        yield draft(pin=value)
    if isinstance(pin, dict):
        for key, value in (
            ("syntax", "fail"),
            ("syntax", None),
            ("semantics_after_font", "fail: x"),
            ("attribute", "data-other"),
            ("attribute", ""),
            ("stylistic_set", 1),
            ("stylistic_set", "ss03"),
            ("expect", 5),
        ):
            yield draft(pin={**pin, key: value})
    policy = drafted.get("policy")
    if not isinstance(policy, dict):
        policy = {
            "file": "glyph_data/runes/qsPea.yaml",
            "keypath": "policy.refuse[+]",
            "suggested_record": "{}",
            "decided_stage": "refuse",
            "why_stub": "why",
            "names_provenance": [],
            "schema_valid": True,
        }
        yield draft(policy=policy)
        yield draft(policy="policy")
    names = policy.get("names_provenance")
    named = list(names) if isinstance(names, list) else []
    for key, value in (
        ("keypath", "policy.other[+]"),
        ("keypath", None),
        ("schema_valid", False),
        ("schema_valid", 1),
        ("names_provenance", [*named, "elsewhere"]),
        ("names_provenance", [["x"]]),
        ("names_provenance", "x"),
        ("names_provenance", []),
        ("why_stub", ""),
    ):
        yield draft(policy={**policy, key: value})
    yield put(provenance=[["x"]], drafts={**drafted, "policy": policy})
    any_of = drafted.get("any_of")
    if not isinstance(any_of, dict):
        return
    candidates = any_of.get("candidates")
    offered = list(candidates) if isinstance(candidates, list) else []
    expect = pin.get("expect") if isinstance(pin, dict) else None
    for value in (offered + offered[:1], [1, True], [["x"]], [1], [None], [expect], [expect, expect], []):
        yield draft(any_of={**any_of, "candidates": value})
    for _text, edge, _features in _expect_edges():
        yield draft(any_of={**any_of, "candidates": [*offered, edge]})
        yield draft(pin=None, any_of={**any_of, "candidates": [edge]})
    for key, value in (("text", ""), ("text", 5), ("features", []), ("features", None)):
        yield draft(any_of={**any_of, key: value})
    yield draft(any_of="any_of")
    yield draft(pin=None)


def _check_generated(_source: Source) -> Iterator[Mapping[str, object]]:
    """The `check` stage's generated lines, under `generated:` and a digest of the fragment: the generic (`_check_mutations`) and targeted (`_check_extras`) mutations of the smallest drafted fragment of each shape the run checked (`_CHECK_DRAFTED`), each checked by `check_unit` at `DRAFTED`, with what it returns or the class of the exception it raises."""
    for shape in sorted(_CHECK_DRAFTED):
        base = _CHECK_DRAFTED[shape][1]
        for mutated in chain(_check_mutations(base), _check_extras(base)):
            yield _check_line("generated", mutated)


# The worker's stage registry. The task that ports a stage adds the stage here (the plan's § Working rules).
STAGES: dict[str, Stage[Source]] = {
    "wire": Stage("wire", {"_phase1_unit": _wire_unit}, _wire_generated),
    "pyfmt": Stage(
        "pyfmt",
        {
            "_ink_readings": _pyfmt_ink_readings,
            "_cluster_id_from_repr": _pyfmt_cluster_head,
            "carry_projection": _pyfmt_carry_projection,
            "_fragment_bytes": _pyfmt_fragment_bytes,
        },
        _pyfmt_generated,
    ),
    "yaml": Stage("yaml", {"yaml.safe_dump": _yaml_safe_dump}, _yaml_generated),
    "shape": Stage("shape", {"Shaper.shape": _shape_call}),
    "ink": Stage("ink", {"_phase1_unit": _ink_unit, "_ink_readings": _ink_diffs}, _ink_generated),
    "settle": Stage(
        "settle", {"Enricher.explain_units": _settle_units, "Enricher.formed_spans": _settle_spans}
    ),
    "explain": Stage(
        "explain",
        {"ExplainReport.render": _explain_render, "_filter_explain": _explain_filter},
        outputs=("rendered", "filtered"),
    ),
    "subset": Stage("subset", {"SubsetPack.row": _subset_row}, _subset_generated),
    "enrich": Stage(
        "enrich",
        {"Enricher.enrich": _enrich_unit},
        _enrich_generated,
        outputs=ENRICH_OUTPUTS,
        field_groups={"geometry": ENRICH_GEOMETRY},
    ),
    "scaffold": Stage(
        "scaffold",
        {"_phase1_unit": _scaffold_unit, "unit_scaffold": _scaffold_returned},
        _scaffold_generated,
    ),
    "expect": Stage(
        "expect",
        {
            "expect_string": _expect_string,
            "Drafter.validate_semantics": _expect_replay,
            "Drafter.draft_any_of": _expect_any_of,
        },
        _expect_generated,
    ),
    "schema": Stage("schema", {"_SchemaValidator.check": _schema_check}, _schema_generated),
    "draft": Stage("draft", {"_phase1_unit": _draft_unit}),
    "fragment": Stage(
        "fragment",
        {"_phase1_unit": _fragment_unit, "_fragment_bytes": _fragment_bytes_returned},
        answer_checks={"packed": _fragment_packed},
    ),
    "check": Stage("check", {"check_unit": _check_unit}, _check_generated),
}


class _Sink:
    """The open `STAGE.ndjson` files of one run under `out`, each line a JSON object whose `from` says whether the run or the stage's generator produced it and whose other fields are typed values. A line whose key a stage already wrote is written once: a second, equal line is dropped, and a different one raises ValueError. A boundary stage's lines are its `Boundary`'s at the stride `every`."""

    def __init__(self, out: Path, stages: Sequence[Stage[Any]], every: int = 1) -> None:
        self._handles: dict[str, IO[str]] = {
            stage.name: (out / f"{stage.name}.ndjson").open("w", encoding="ascii") for stage in stages
        }
        self._written: dict[str, dict[object, bytes]] = {stage.name: {} for stage in stages}
        self._kinds = {stage.name: stage.kind for stage in stages}
        self._every = every
        self._lock = threading.Lock()

    def record(
        self,
        stage: str,
        recorded: Mapping[str, object] | list[Mapping[str, object]] | Boundary | WalkedBoundary,
    ) -> None:
        """Write what one of the run's calls recorded for `stage`: a call stage's line or lines, or a boundary stage's `Boundary` or `WalkedBoundary`; either kind recording the other's form raises TypeError."""
        if isinstance(recorded, Boundary | WalkedBoundary) != (self._kinds[stage] == "boundary"):
            raise TypeError(f"the {stage} stage records {self._kinds[stage]} lines, not {recorded!r:.80}")
        if isinstance(recorded, Boundary | WalkedBoundary):
            lines: Iterable[Mapping[str, object]] = recorded.lines(self._every)
        else:
            lines = recorded if isinstance(recorded, list) else [recorded]
        for line in lines:
            self.write(stage, "run", line)

    def write(self, stage: str, origin: str, fields: Mapping[str, object]) -> None:
        text = compact({"from": origin} | {name: encode(value) for name, value in fields.items()})
        digest = hashlib.sha1(text.encode("ascii")).digest()
        key = fields.get("key")
        with self._lock:
            written = self._written[stage]
            if key in written:
                if written[key] != digest:
                    raise ValueError(f"the {stage} stage recorded two different lines under the key {key!r}")
                return
            written[key] = digest
            self._handles[stage].write(text + "\n")

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()


def _resolve(hook: str, hooks: Mapping[str, tuple[str, str]]) -> tuple[object, str]:
    module_name, path = hooks[hook]
    owner: object = importlib.import_module(module_name)
    *parents, attribute = path.split(".")
    for parent in parents:
        owner = getattr(owner, parent)
    return owner, attribute


@contextlib.contextmanager
def wrapped(
    stages: Sequence[Stage[Any]], sink: _Sink | None, hooks: Mapping[str, tuple[str, str]] = HOOKS
) -> Iterator[None]:
    """Replace each hook point of `hooks` that one of `stages` records with a wrapper that calls the original and passes the call to each stage's recorder for that hook, and restore the originals on exit; a hook point no stage records is left as it is, so the hot calls of a stage not recording cost the build nothing. A stage recorder for a hook `hooks` does not name raises ValueError."""
    unhooked = sorted({hook for stage in stages for hook in stage.recorders if hook not in hooks})
    if unhooked:
        raise ValueError(f"no hook point is registered for {', '.join(unhooked)}")
    restore: list[tuple[object, str, object]] = []
    try:
        for hook in hooks:
            recorders = [(stage.name, stage.recorders[hook]) for stage in stages if hook in stage.recorders]
            if not recorders:
                continue
            owner, attribute = _resolve(hook, hooks)
            original = getattr(owner, attribute)
            setattr(owner, attribute, _wrapper(original, recorders, sink))
            restore.append((owner, attribute, original))
        yield
    finally:
        for owner, attribute, original in reversed(restore):
            setattr(owner, attribute, original)


def _wrapper(original: Callable, recorders: list[tuple[str, Recorder]], sink: _Sink | None) -> Callable:
    @functools.wraps(original)
    def call(*args, **kwargs):
        finishers = [
            (name, finish) for name, record in recorders if (finish := record(args, kwargs)) is not None
        ]
        result = original(*args, **kwargs)
        for name, finish in finishers:
            recorded = finish(result)
            if recorded is not None and sink is not None:
                sink.record(name, recorded)
        return result

    return call


# --- running the worker -------------------------------------------------------------------------


class _Pipe:
    """The worker's end of its pipe, in process: `recv` hands out one `phase1` message per handout, materialized as it is asked for and passed through multiprocessing's pickler as `Connection.send` and `recv` pass it, then `phase1-done` and `stop`; `send` takes each reply to `on_batch`."""

    def __init__(self, source: Source, on_batch: Callable[[list, list[str]], None]) -> None:
        self._source = source
        self._on_batch = on_batch
        self._next = 0
        self._done = False
        self.peak = 0

    def recv(self) -> tuple:
        if self._next < len(self._source.handouts):
            units = self._source.units(self._source.handouts[self._next])
            self._next += 1
            return pickle.loads(ForkingPickler.dumps(("phase1", units, "w0")))
        if not self._done:
            self._done = True
            return ("phase1-done",)
        return ("stop",)

    def send(self, reply: tuple) -> None:
        reply = pickle.loads(ForkingPickler.dumps(reply))
        if reply[0] == "batch":
            self._on_batch(reply[1], reply[2])
        elif reply[0] == "peak":
            self.peak = reply[1]
        elif reply[0] == "error":
            raise RuntimeError("the corpus worker failed:\n" + reply[1])

    def close(self) -> None:
        pass


@dataclass
class RunResult:
    """What one worker run returned: the units, a sha256 over every projection's and every batch's contract errors' typed encoding in reply order, and the projections that did not survive the typed encoding's round trip, by ordinal."""

    units: int = 0
    digest: str = ""
    unfaithful: list[int] = field(default_factory=list)
    contract_errors: int = 0


def run_worker(source: Source) -> RunResult:
    """Run `build._corpus_worker` over the source's handouts in this process, as a pool worker runs them, and return the run's digest and round-trip result. A worker failure raises RuntimeError with its traceback."""
    digest = hashlib.sha256()
    result = RunResult()

    def on_batch(projections: list, contract_errors: list[str]) -> None:
        for projection in projections:
            text = compact(encode(projection))
            digest.update(text.encode("ascii") + b"\n")
            if not same(projection, decode(json.loads(text))):
                result.unfaithful.append(projection.ordinal)
            result.units += 1
        digest.update(compact(encode(contract_errors)).encode("ascii") + b"\n")
        result.contract_errors += len(contract_errors)

    pipe = _Pipe(source, on_batch)
    build._corpus_worker(pipe, source.init)
    if not pipe.peak:
        raise RuntimeError("the corpus worker stopped without answering the stop message")
    result.digest = digest.hexdigest()
    return result


# --- the build scenarios ------------------------------------------------------------------------

SCENARIO_ROOT = REPO_ROOT / "var" / "rust-port" / "build-scenarios"
MINI_SPEC = SCENARIO_ROOT / "mini-spec"
MINI_BASE = SCENARIO_ROOT / "mini-base"
LIVE_BASE = SCENARIO_ROOT / "live-base"
# Each build scenario and the sources it runs on (`rebuild/CORPUS-PARENT-PORT-PLAN.md` § Scenarios).
BUILD_SCENARIOS: dict[str, tuple[str, ...]] = {
    "first": ("mini",),
    "cold": ("mini", "live"),
    "warm": ("mini", "live"),
    "incremental": ("mini", "live"),
    "rebase": ("mini", "live"),
    "restamped": ("mini",),
    "rederived": ("mini",),
    "relocate": ("mini",),
    "torn": ("mini",),
    "chain": ("mini",),
    "refusals": ("mini",),
}
# The scenario `refusals` runs only by name; `--scenario all` names every other scenario of the source.
BY_NAME_ONLY = ("refusals",)
BUILD_REQUEST_FORMAT = "ams-review-build-scenario-request/1"
# The audit row's fields the scenarios' edits read: the window, the matched entry, and the two rendered-name fields.
_WINDOW, _ENTRY, _BASELINE, _NEW = 1, 3, 4, 5
# The ledger class the mini's edited audit retags windows into, as `rebuild/test_unit_cache.py`'s `_edited_audit` does.
RETAG_CLASS = "identical-unused-join-name"
# The output whose fold claim a chain step's pass holds when its claim is held elsewhere: the windows rows, which claim before the corpus build in a cycle (`row_delta.claim_fold`).
HELD_CLAIM_OUTPUT = "windows-default.tsv.gz"


@dataclass(frozen=True)
class BuildInputs:
    """What one Python build of a scenario reads, as `build.build_m1`'s arguments: the audit, the ledger, the subset tables and their pack, the after font, the spec root, and whether it recomputes every unit."""

    audit: Path
    ledger: Path
    subset_dir: Path
    subset_pack: Path
    after_font: Path
    spec_root: Path
    recompute_all: bool = False

    def request(self) -> dict:
        return {
            item.name: str(value) if isinstance(value, Path) else value
            for item in dataclasses.fields(self)
            for value in (getattr(self, item.name),)
        }

    @classmethod
    def from_request(cls, request: Mapping[str, object]) -> BuildInputs:
        return cls(
            Path(str(request["audit"])),
            Path(str(request["ledger"])),
            Path(str(request["subset_dir"])),
            Path(str(request["subset_pack"])),
            Path(str(request["after_font"])),
            Path(str(request["spec_root"])),
            bool(request["recompute_all"]),
        )


@dataclass
class BuildRun:
    """One Python build as `run_python_build` ran it: its label, the tree it started from, the tree it wrote, its exit status, what it printed, and the cycle directory of a pass whose claim another output holds."""

    label: str
    prior: Path
    out: Path
    returncode: int
    log: str
    cycle_var: Path | None = None

    @functools.cached_property
    def notes(self) -> dict[str, str]:
        """Each `[t] review.build <phase>` line's note, by phase."""
        notes: dict[str, str] = {}
        for line in self.log.splitlines():
            if line.startswith("[t] review.build "):
                head, _, note = line.partition("\t")
                notes[head.split()[2]] = note
        return notes

    def note(self, phase: str) -> str:
        return self.notes.get(phase, "")

    def warns(self) -> list[str]:
        return [line[len("[warn] ") :] for line in self.log.splitlines() if line.startswith("[warn] ")]

    def says(self, text: str) -> bool:
        return any(line == text for line in self.log.splitlines())

    def counts(self, phase: str, pattern: str) -> tuple[int, ...] | None:
        """The integers `pattern`'s groups match in the phase's note, commas dropped, or None when it does not match."""
        found = re.search(pattern, self.note(phase))
        return None if found is None else tuple(int(group.replace(",", "")) for group in found.groups())

    def message(self) -> str:
        """The last line the build printed, which is a refusal's text when it stopped with one."""
        lines = self.log.rstrip("\n").splitlines()
        return lines[-1] if lines else ""


# A check sees one case's run and returns the problems it finds: each a sentence naming what the build did not reach.
BuildCheck = Callable[[BuildRun], list[str]]


@dataclass(frozen=True)
class BuildCase:
    """One build of a scenario: its name, what it reads, the prior corpus it builds over (a clone of `prior`, or, when `prior` is None, of the output of the case before it), its units width (None for the harness's default, `default_width`), the output that holds its pass's fold claim when the pass runs as a cycle's child whose claim another output took (`row_delta.claim_fold`), and the check that the Python build reached what the scenario's row names."""

    name: str
    inputs: BuildInputs
    prior: Path | None
    check: BuildCheck
    jobs: int | None = None
    claim_held_by: str | None = None


@dataclass(frozen=True)
class BuildScenario:
    """A prepared scenario: its source and name, the directory that holds its priors and its changed inputs, and its builds in order."""

    source: str
    name: str
    directory: Path
    cases: tuple[BuildCase, ...]


@dataclass(frozen=True)
class BasePrior:
    """A source's base prior: the corpus every scenario starts from and the inputs its build read, which every scenario's build reads except where its row changes them."""

    source: str
    out: Path
    inputs: BuildInputs


def default_width() -> int:
    """The harness's default units width: what a `--skip-gates` cycle passes the build."""
    from rebuild.tools.artifact_cycle import corpus_job_budget

    return corpus_job_budget(skip_gates=True)


def signature_width(jobs: int | None) -> int:
    """The signature shaping's width for a build at `jobs` units jobs (the harness's default when None): one for a build at one job, and a `--skip-gates` cycle's otherwise."""
    if jobs == 1:
        return 1
    from rebuild.tools.artifact_cycle import signature_job_budget

    return signature_job_budget()


def _clone_file(source: Path, target: Path) -> None:
    """Copy one file with its modification time, as a copy-on-write clone where the volume can make one."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        result = subprocess.run(
            ["cp", "-c", "-p", str(source), str(target)], capture_output=True, check=False
        )
        if result.returncode == 0:
            return
    shutil.copy2(source, target)


def _clone_tree(source: Path, target: Path) -> None:
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    landing.clone_tree(source, target)


def _pin_time(path: Path, like: Path) -> None:
    """Give `path` the modification time of `like`, which `build._generated_at` reads, so a scenario's stamp does not move with the run that wrote the file."""
    os.utime(path, ns=(path.stat().st_atime_ns, like.stat().st_mtime_ns))


def _build_environment(counts: Path, run: str | None) -> dict[str, str]:
    """The environment a scenario's build child runs in, as the cycle's step runs the build: the Rust units engine, no debug tally, the counts file named, no cycle run unless the case's pass holds one, and on macOS the allocator's large cache off."""
    environment = dict(os.environ)
    environment[units_exec.ENGINE_VARIABLE] = "rust"
    environment.pop(memory_tally.TALLY_ENV, None)
    environment.pop(row_delta.CYCLE_RUN_ENV, None)
    environment[output_sink.OUTPUT_COUNTS_ENV] = str(counts)
    if run is not None:
        environment[row_delta.CYCLE_RUN_ENV] = run
    if sys.platform == "darwin":
        environment.update(KERNEL_ALLOCATOR_ENVIRONMENT)
    return environment


def run_python_build(
    inputs: BuildInputs,
    out: Path,
    logs: Path,
    label: str,
    *,
    prior: Path | None = None,
    jobs: int | None = None,
    claim_held_by: str | None = None,
) -> BuildRun:
    """Run one Python build of `inputs` into `out` in a fresh process (`python_build_child`), as the cycle's step runs it, and return the run. `out` is first made a clone of `prior` when one is given, and otherwise built in place. `logs` holds the request, the log of standard error, the counts file, the journal the build writes its records to, and the cycle directory of a pass whose claim `claim_held_by` holds. `jobs` is the units width, the harness's default when None, and a build at one job shapes its signatures at one job too."""
    logs.mkdir(parents=True, exist_ok=True)
    if prior is not None:
        _clone_tree(prior, out)
    width = default_width() if jobs is None else jobs
    signature_jobs = signature_width(jobs)
    counts = logs / f"{label}.counts"
    counts.write_bytes(b"")
    run = None
    cycle_var = None
    if claim_held_by is not None:
        run = f"build-scenarios-{label}"
        cycle_var = logs / f"{label}-cycle"
        cycle_var.mkdir(parents=True, exist_ok=True)
        (cycle_var / cycle_paths.FOLD_CLAIM_NAME).write_text(
            json.dumps({"run": run, "output": claim_held_by}) + "\n", encoding="utf-8"
        )
    request = {
        "format": BUILD_REQUEST_FORMAT,
        "out": str(out),
        "inputs": inputs.request(),
        "jobs": width,
        "signature_jobs": signature_jobs,
        "journal": str(logs / "journal.ndjson"),
        "cycle_var": None if cycle_var is None else str(cycle_var),
    }
    request_path = logs / f"{label}.json"
    request_path.write_text(json.dumps(request, indent=1) + "\n", encoding="utf-8")
    log_path = logs / f"{label}.log"
    with log_path.open("wb") as log:
        process = subprocess.run(
            [sys.executable, "-m", "rebuild.tools.units_stage_fixtures", "python-build", str(request_path)],
            cwd=REPO_ROOT,
            env=_build_environment(counts, run),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return BuildRun(
        label,
        prior if prior is not None else out,
        out,
        process.returncode,
        log_path.read_text(encoding="utf-8", errors="replace"),
        cycle_var,
    )


def python_build_child(request_path: Path) -> int:
    """Run the build one `run_python_build` request describes, in this process: the journal and, for a pass whose claim another output holds, the cycle directory redirected as the request names them."""
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("format") != BUILD_REQUEST_FORMAT:
        raise SystemExit(f"{request_path} is not a {BUILD_REQUEST_FORMAT} request")
    cycle_timings.JOURNAL = Path(request["journal"])
    if request["cycle_var"] is not None:
        cycle_paths.CYCLE_VAR = Path(request["cycle_var"])
    inputs = BuildInputs.from_request(request["inputs"])
    build.build_m1(
        Path(request["out"]),
        audit_path=inputs.audit,
        ledger_path=inputs.ledger,
        subset_dir=inputs.subset_dir,
        after_font=inputs.after_font,
        jobs=request["jobs"],
        signature_jobs=request["signature_jobs"],
        recompute_all_units=inputs.recompute_all,
        spec_root=inputs.spec_root,
        subset_pack=inputs.subset_pack,
    )
    return 0


_BASES: dict[str, BasePrior] = {}


def base_prior(source: str) -> BasePrior:
    """The source's base prior, prepared once a process (`_mini_base`, `_live_base`)."""
    if source not in _BASES:
        _BASES[source] = _mini_base() if source == "mini" else _live_base()
    return _BASES[source]


def _required_run(run: BuildRun, what: str) -> BuildRun:
    if run.returncode != 0:
        raise SystemExit(f"{what} failed with status {run.returncode}: {run.message()}")
    return run


def _mini_base() -> BasePrior:
    """Materialize the mini bundle's spec at the fixed `MINI_SPEC`, pack its subset tables there and pin its ledger's modification time to the bundle audit's, so every build of a run reads one spec and stamps one `generated_at`; then build the bundle from an empty directory into `MINI_BASE`, the `first` build."""
    shutil.rmtree(MINI_SPEC, ignore_errors=True)
    spec_root = pin.materialize(MINI_SPEC)
    subset_pack = spec_root / PACK_NAME
    write_pack_in_memory(MINI, ACCEPTANCE_CONFIGS, table_digests(MINI, ACCEPTANCE_CONFIGS), subset_pack)
    audit_path = MINI / "audit.tsv"
    ledger = spec_root / pin.LEDGER_PATH
    _pin_time(ledger, audit_path)
    inputs = BuildInputs(audit_path, ledger, MINI, subset_pack, MINI / "M1.otf", spec_root)
    shutil.rmtree(MINI_BASE, ignore_errors=True)
    empty = MINI_BASE / "empty"
    empty.mkdir(parents=True)
    out = MINI_BASE / "out"
    _required_run(
        run_python_build(inputs, out, MINI_BASE / "logs", "base", prior=empty), "the mini base prior's build"
    )
    return BasePrior("mini", out, inputs)


def _audit_names(audit_path: Path) -> list[str]:
    """The files that hold an audit, beside one another: its head and every part the head lists, or the single file a tree with no head holds."""
    head = row_delta.read_head(audit_path.parent, audit_path.name)
    if head is None:
        return [audit_path.name]
    return [row_delta.head_name(audit_path.name), *(part.name for part in head.parts)]


def _clone_live_inputs(target: Path) -> None:
    """Clone what the build reads under `rebuild/out/m1/` and the ledger into `target`, modification times kept: the audit's head and every part it lists (or its single file), the after font, the subset tables and their pack, and the Stage A record the manifest copies."""
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    m1 = build.M1_SUBSETS
    names = _audit_names(build.M1_AUDIT)
    names += [build.M1_AFTER_FONT.name, PACK_NAME, fingerprint.STAGE_A_FILENAME]
    names += [f"baseline-{config}.subset.tsv.gz" for config in ACCEPTANCE_CONFIGS]
    for name in names:
        if (m1 / name).is_file():
            _clone_file(m1 / name, target / name)
    _clone_file(build.M1_LEDGER, target / build.M1_LEDGER.name)


def _live_base() -> BasePrior:
    """Refresh the live base prior under `LIVE_BASE`: clone `rebuild/out/review` into its `out/` the first time (`landing.clone_tree`), clone the build's inputs into its `inputs/` again (`_clone_live_inputs`), and run one Python build at the current tree into `out/`, so every live scenario starts from the engine of record's output for the tree as it stands."""
    for needed in (build.DEFAULT_OUT / "manifest.json", build.M1_AFTER_FONT):
        if not needed.is_file():
            raise SystemExit(f"the live source needs the last cycle's {needed.relative_to(REPO_ROOT)}")
    out = LIVE_BASE / "out"
    if not out.is_dir():
        _clone_tree(build.DEFAULT_OUT, out)
    inputs_dir = LIVE_BASE / "inputs"
    _clone_live_inputs(inputs_dir)
    inputs = BuildInputs(
        inputs_dir / build.M1_AUDIT.name,
        inputs_dir / build.M1_LEDGER.name,
        inputs_dir,
        inputs_dir / PACK_NAME,
        inputs_dir / build.M1_AFTER_FONT.name,
        REPO_ROOT,
    )
    _required_run(
        run_python_build(inputs, out, LIVE_BASE / "logs", "refresh"), "the live base prior's refresh"
    )
    return BasePrior("live", out, inputs)


# --- changed inputs and priors ------------------------------------------------------------------


def _audit_rows(path: Path) -> tuple[str, list[list[str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines[0], [line.split("\t") for line in lines[1:]]


def _write_audit(path: Path, header: str, rows: Iterable[Sequence[str]], like: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([header, *("\t".join(row) for row in rows)]) + "\n", encoding="utf-8")
    _pin_time(path, like)
    return path


def _windows(rows: Sequence[Sequence[str]]) -> dict[str, list[Sequence[str]]]:
    windows: dict[str, list[Sequence[str]]] = {}
    for row in rows:
        windows.setdefault(row[_WINDOW], []).append(row)
    return windows


def _units_of(rows: Sequence[Sequence[str]]) -> list[list[Sequence[str]]]:
    """A window's rows grouped into its units, by (baseline, new) in the order the file first names them."""
    units: dict[tuple[str, str], list[Sequence[str]]] = {}
    for row in rows:
        units.setdefault((row[_BASELINE], row[_NEW]), []).append(row)
    return list(units.values())


def edited_rows(rows: Sequence[Sequence[str]]) -> list[Sequence[str]]:
    """The audit rows with some windows' rows changed and some windows removed. It drops the fourth window and the second window that holds two units (a sibling window, whose ink-signature entries go unhit); retags every row of the first two windows from the fifth on that are not in `RETAG_CLASS` into it, as `rebuild/test_unit_cache.py`'s `_edited_audit` does, which changes their input keys and moves them to that class's overflow part; and, in the first sibling window whose second unit holds two rows or more, moves the last row of that unit into its first, its matched entry and both rendered-name fields taken from the first unit's first row. That row's signature key is one the prior store does not hold, so the key-only read misses it, and the unit it joins keeps its first configuration, the one the enricher re-settles."""
    windows = _windows(rows)
    order = list(windows)
    siblings = [window for window in order if len(_units_of(windows[window])) > 1]
    moved_window = next(
        window for window in siblings if len(units := _units_of(windows[window])) > 1 and len(units[1]) > 1
    )
    dropped = {order[3], next(window for window in siblings if window != moved_window)}
    retagged = [
        window
        for window in order[4:]
        if window not in dropped
        and window != moved_window
        and all(row[_ENTRY] != RETAG_CLASS for row in windows[window])
    ][:2]
    first_unit, second_unit = _units_of(windows[moved_window])[:2]
    moved = second_unit[-1]
    edited: list[Sequence[str]] = []
    for row in rows:
        if row[_WINDOW] in dropped:
            continue
        if row[_WINDOW] in retagged:
            row = [*row[:_ENTRY], RETAG_CLASS, *row[_ENTRY + 1 :]]
        elif row is moved:
            row = [*row[:_ENTRY], first_unit[0][_ENTRY], first_unit[0][_BASELINE], first_unit[0][_NEW]]
        edited.append(row)
    return edited


def edited_audit(audit_path: Path, target: Path) -> Path:
    """Write the mini audit as `edited_rows` changes it, its modification time the bundle audit's, and return it."""
    header, rows = _audit_rows(audit_path)
    return _write_audit(target, header, edited_rows(rows), audit_path)


def diverging_audit(audit_path: Path, target: Path) -> Path:
    """Write the mini audit with one unit's cells changed so the enricher's re-settlement diverges from them, which fails the build's contract check: in the first window that holds two or more units, the first of which holds only cell tokens in `new`, that unit's rows each take, in place of their first token, the first token of the first such row that names another family. The rows' signature keys move with them, so the key-only read misses them and the build rewrites the signature store."""
    header, rows = _audit_rows(audit_path)

    def cells(row: Sequence[str]) -> bool:
        return all("/" in token for token in row[_NEW].split("|"))

    unit = next(
        units[0]
        for held in _windows(rows).values()
        if len(units := _units_of(held)) > 1 and cells(units[0][0])
    )
    first = unit[0][_NEW].split("|")[0]
    other = next(
        token
        for row in rows
        if cells(row) and (token := row[_NEW].split("|")[0]).split("/")[0] != first.split("/")[0]
    )
    changed = {id(row) for row in unit}
    edited = [
        [*row[:_NEW], "|".join([other, *row[_NEW].split("|")[1:]])] if id(row) in changed else row
        for row in rows
    ]
    return _write_audit(target, header, edited, audit_path)


# How many of the live audit's first windows, in its first configuration's order, the scratch segment's edits choose among.
SCRATCH_WINDOWS = 4096


def scratch_audit_segment(audit_path: Path, target_dir: Path) -> Path:
    """Clone a live audit's parts into `target_dir` beside a scratch head that lists them and one more segment, which this writes as the audit's writer writes one (`oracle.AuditWriter`: plain-keyed upserts and tombstones in the audit's order, a configuration a gzip member, `row_delta.MemberPart`), its `content` one more, and return the audit path the build reads. The segment makes `edited_rows`' edits to the audit's first `SCRATCH_WINDOWS` windows, every configuration's rows of them, so every row it holds is settled under the current spec."""
    head = row_delta.read_head(audit_path.parent, audit_path.name)
    if head is None:
        raise SystemExit(f"{audit_path} has no head to add a segment to")
    target_dir.mkdir(parents=True, exist_ok=True)
    for part in head.parts:
        _clone_file(audit_path.parent / part.name, target_dir / part.name)
    chosen: dict[str, None] = {}
    first: str | None = None
    rows: list[Sequence[str]] = []
    pending = b""
    for chunk in oracle.materialized_audit(audit_path.parent):
        lines = (pending + chunk).split(b"\n")
        pending = lines.pop()
        for line in lines:
            fields = line.decode("utf-8").split("\t")
            if fields[0] == "config":
                continue
            if first is None:
                first = fields[0]
            if fields[0] == first and len(chosen) < SCRATCH_WINDOWS:
                chosen.setdefault(fields[_WINDOW])
            if fields[_WINDOW] in chosen:
                rows.append(fields)
    before = {(row[0], row[_WINDOW]): row for row in rows}
    after = {(row[0], row[_WINDOW]): row for row in edited_rows(rows)}
    changes = sorted(
        [
            *(b"+\t" + "\t".join(row).encode() + b"\n" for key, row in after.items() if before[key] != row),
            *(b"-\t" + "\t".join(key).encode() + b"\n" for key in before if key not in after),
        ],
        key=lambda line: oracle.audit_key(line[2:]),
    )
    number = len(head.segments) + 1
    writer = row_delta.MemberPart(
        target_dir,
        row_delta.segment_name(audit_path.name, head.generation, number),
        row_delta.segment_header(audit_path.name, head.generation, number, row_delta.MERGE_PLAIN_KEY),
        field=1,
    )
    for line in changes:
        writer.write(line)
    part = writer.close()
    assert part is not None
    scratch = row_delta.with_segment(head, part, head.stamps)
    (target_dir / row_delta.head_name(audit_path.name)).write_text(
        json.dumps(scratch.document(), indent=1) + "\n", encoding="utf-8"
    )
    return target_dir / audit_path.name


def _manifest_classes(corpus: Path) -> list[dict]:
    return json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))["classes"]


def _class_units(corpus: Path, class_id: str, limit: int | None = None) -> list[dict]:
    """The units index's records of one class in the prior corpus, in index order, read until the class's run of records ends or `limit` are held."""
    opened = unit_index.open_index(corpus)
    if opened is None:
        raise SystemExit(f"{corpus} has no units index")
    marker = json.dumps({"class": class_id})[1:-1].encode()
    found: list[dict] = []
    with opened:
        for line in opened.lines():
            if marker in line:
                record = json.loads(line)
                if record["class"] == class_id:
                    found.append(record)
                    if len(found) == limit:
                        break
                    continue
            if found:
                break
    return found


def _exemplar_key(record: Mapping) -> tuple[str, str]:
    return record["configs"][0], record["codepoints"]


def _ledger_entries(ledger: Path) -> list[dict]:
    return yaml.safe_load(ledger.read_text(encoding="utf-8")) or []


def _write_ledger(path: Path, entries: Sequence[Mapping], like: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(list(entries), sort_keys=False, allow_unicode=True, width=1_000_000), encoding="utf-8"
    )
    _pin_time(path, like)
    return path


@dataclass(frozen=True)
class LedgerPlan:
    """The ledger classes a scenario edits, chosen from the base prior: `small`, the smallest ledger class present, whose `no_verdict` an edit flips, and `large`, the largest ledger class present that takes full fragments, with its units' exemplar keys in index order (the first `limit` of them, when `ledger_plan` is given one), whose exemplar flags an edit moves."""

    small: str
    large: str
    large_keys: tuple[tuple[str, str], ...]


def ledger_plan(corpus: Path, ledger: Path, limit: int | None = None) -> LedgerPlan:
    ids = {entry["id"] for entry in _ledger_entries(ledger)}
    present = [meta for meta in _manifest_classes(corpus) if meta["id"] in ids]
    small = min(present, key=lambda meta: meta["unit_count"])
    large = max((meta for meta in present if not meta["no_verdict"]), key=lambda meta: meta["unit_count"])
    return LedgerPlan(
        small["id"],
        large["id"],
        tuple(_exemplar_key(record) for record in _class_units(corpus, large["id"], limit)),
    )


def _flip_no_verdict(entry: dict) -> None:
    entry["no_verdict"] = not entry.get("no_verdict", False)


def _with_exemplars(entry: dict, keys: Iterable[tuple[str, str]]) -> None:
    entry["exemplars"] = [{"config": config, "codepoints": codepoints} for config, codepoints in keys]


def incremental_ledger(base: BasePrior, plan: LedgerPlan, target: Path) -> Path:
    """Write the base ledger with the small class's units turned slim or full (its `no_verdict` flipped) and the large class's first exemplar moved to its first unit no exemplar names, its modification time the base ledger's, and return it."""
    entries = _ledger_entries(base.inputs.ledger)
    taken = {(item["config"], item["codepoints"]) for entry in entries for item in entry.get("exemplars", ())}
    for entry in entries:
        if entry["id"] == plan.small:
            _flip_no_verdict(entry)
        if entry["id"] == plan.large:
            fresh = next(key for key in plan.large_keys if key not in taken)
            kept = [(item["config"], item["codepoints"]) for item in entry.get("exemplars", ())][1:]
            _with_exemplars(entry, [fresh, *kept])
    return _write_ledger(target, entries, base.inputs.ledger)


def _moved_line(lines: Sequence[str], label: str) -> list[str]:
    """`lines` with the digest of the line labeled `label` replaced by another, as a move of that input gives it."""
    moved = []
    found = False
    for line in lines:
        name, _, value = line.partition("\t")
        if name == label:
            value = hashlib.sha256(f"moved {value}".encode()).hexdigest()
            found = True
        moved.append(f"{name}\t{value}")
    if not found:
        raise SystemExit(f"the stamp has no {label} line to move: {list(lines)}")
    return moved


def move_store_stamp(corpus: Path, label: str) -> None:
    """Move the `label` line of the unit store's head stamps, and the `environment` digest over the lines with it."""
    head = row_delta.read_head(corpus, unit_cache.STORE_NAME)
    if head is None:
        raise SystemExit(f"{corpus} has no unit store head")
    lines = _moved_line(head.stamps["environment_lines"], label)
    stamps = {
        **head.stamps,
        "environment": fingerprint.EnvironmentStamp(tuple(lines)).value,
        "environment_lines": lines,
    }
    row_delta.commit(corpus, dataclasses.replace(head, stamps=stamps))


def move_signature_stamp(corpus: Path, label: str) -> None:
    """Move the `label` line of the ink-signature store's header, and the `environment` digest over the lines with it, keeping its entries."""
    path = unit_cache.signature_store_path(corpus)
    header_line, _, entries = gzip.decompress(path.read_bytes()).partition(b"\n")
    header = json.loads(header_line)
    lines = _moved_line(header["environment_lines"], label)
    header["environment"] = fingerprint.EnvironmentStamp(tuple(lines)).value
    header["environment_lines"] = lines
    with gzip.GzipFile(path, "wb", compresslevel=1, mtime=0) as stream:
        stream.write(json.dumps(header).encode() + b"\n" + entries)


def relocate_parts(corpus: Path) -> list[str]:
    """Rewrite every shard part of the corpus as compact JSON, so every part's size disagrees with the one the unit store recorded while each fragment stays readable by the walk, and return the parts."""
    recorded = (unit_cache.store_stamps(corpus) or {}).get("parts") or {}
    parts = [part for meta in _manifest_classes(corpus) for part in unit_index.class_shards(meta)]
    for part in parts:
        path = corpus / part
        path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8"))), encoding="utf-8")
        if path.stat().st_size == recorded.get(part):
            raise SystemExit(f"{part} kept the size the store recorded for it once rewritten")
    return parts


def _store_files(corpus: Path) -> list[str]:
    head = row_delta.read_head(corpus, unit_cache.STORE_NAME)
    if head is None:
        raise SystemExit(f"{corpus} has no unit store head")
    return [row_delta.head_name(unit_cache.STORE_NAME), *(part.name for part in head.parts)]


def tear(corpus: Path, base: Path) -> None:
    """Make `corpus`, an incremental build's output, what a pass stopped after its facts line leaves beside a later build's: the base prior's unit store (head and parts) and `review-facts.json` put back, with a leftover staged overflow part of the first class that has one (or the first class) and a leftover staged ink-signature store, each a cut copy of what it stands for."""
    for name in _store_files(corpus):
        (corpus / name).unlink()
    for name in [*_store_files(base), "review-facts.json"]:
        _clone_file(base / name, corpus / name)
    classes = _manifest_classes(corpus)
    torn = next(
        (meta for meta in classes if any(".x" in part for part in unit_index.class_shards(meta))), classes[0]
    )
    first = corpus / unit_index.class_shards(torn)[0]
    staged = corpus / f"{unit_index.overflow_part_name(torn['id'], 0)}{output_sink.STAGING_SUFFIX}"
    staged.write_bytes(first.read_bytes()[: max(1, first.stat().st_size // 2)])
    signatures = unit_cache.signature_store_path(corpus)
    data = signatures.read_bytes()
    signatures.with_name(signatures.name + output_sink.STAGING_SUFFIX).write_bytes(data[: len(data) // 2])


def sampled_record(corpus: Path) -> tuple[int, dict]:
    """The base row and record of the first unit, by id, of the verification sample a build over unchanged inputs draws from the corpus's unit store: the store's id words in ascending order, seeded with its environment stamp (`build._verification_sample`)."""
    header, lines = _store_held(corpus)
    records = [json.loads(line) for line in lines]
    words = sorted(word for record in records if (word := unit_cache.id_word(record["id"])) is not None)
    first = min(build._verification_sample(words, header["environment"]))
    index = next(index for index, record in enumerate(records) if unit_cache.id_word(record["id"]) == first)
    return index, records[index]


def _store_held(corpus: Path) -> tuple[dict, list[bytes]]:
    materialized = unit_cache.materialized_store(corpus)
    if materialized is None:
        raise SystemExit(f"{corpus} has no unit store")
    header, lines = materialized
    return header, list(lines)


def stale_sample_store(corpus: Path) -> str:
    """Change one sampled record's ink deltas by a store segment the tool writes, which leaves the record's content key, so the plan reuses it and the verification sample's recomputation disagrees with it; return its id."""
    head = row_delta.read_head(corpus, unit_cache.STORE_NAME)
    if head is None or head.segments:
        raise SystemExit(
            f"{corpus}'s unit store is not a base alone, so its rows' places are not their lines"
        )
    index, record = sampled_record(corpus)
    record["ink_deltas"] = {**record["ink_deltas"], "bogus": "d-000000000000"}
    line = (json.dumps(record) + "\n").encode()
    part = row_delta.write_segment(
        corpus,
        unit_cache.STORE_NAME,
        head.generation,
        1,
        [row_delta.Row(row_delta.UPSERT, (index, ""), line)],
    )
    row_delta.commit(corpus, row_delta.with_segment(head, part, head.stamps))
    return record["id"]


def corrupt_copied_fragment(corpus: Path) -> str:
    """Change one byte of the id of the first fragment of the first class's first part, keeping the part's size, so the plan trusts its address and the write's byte copy refuses it; return the id."""
    meta = _manifest_classes(corpus)[0]
    path = corpus / unit_index.class_shards(meta)[0]
    raw = path.read_bytes()
    unit_id = json.loads(raw)[0]["id"]
    quoted = f'"id": "{unit_id}"'.encode()
    corrupted = quoted[:-2] + (b"1" if quoted[-2:-1] != b"1" else b"2") + b'"'
    if raw.count(quoted) != 1:
        raise SystemExit(f"{path} names {unit_id} other than once")
    path.write_bytes(raw.replace(quoted, corrupted))
    return unit_id


# --- the scenarios ------------------------------------------------------------------------------


def _scenario_dir(source: str, scenario: str) -> Path:
    return SCENARIO_ROOT / f"{source}-{scenario}"


def _cleared(directory: Path) -> Path:
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    return directory


def open_build_source(source: str, scenario: str) -> BuildScenario:
    """Prepare one scenario of § Scenarios over the source's base prior (`base_prior`): its directory under `SCENARIO_ROOT` cleared, its priors cloned there and changed as its row says, and its changed inputs written beside them; every input its row leaves unchanged is the base prior's own, so a build over unchanged inputs stamps the prior's manifest. Nothing under `rebuild/out/` is written."""
    if source not in SOURCES:
        raise SystemExit(f"unknown source {source!r}; the sources are {', '.join(SOURCES)}")
    if scenario not in BUILD_SCENARIOS:
        raise SystemExit(f"unknown scenario {scenario!r}; the scenarios are {', '.join(BUILD_SCENARIOS)}")
    if source not in BUILD_SCENARIOS[scenario]:
        raise SystemExit(
            f"the {scenario} scenario runs on {', '.join(BUILD_SCENARIOS[scenario])}, not {source}"
        )
    base = base_prior(source)
    directory = _cleared(_scenario_dir(source, scenario))
    prepare = _PREPARERS[scenario]
    return BuildScenario(source, scenario, directory, tuple(prepare(base, directory)))


def _prior(base: BasePrior, directory: Path, name: str = "prior") -> Path:
    target = directory / name
    _clone_tree(base.out, target)
    return target


def _first(base: BasePrior, directory: Path) -> list[BuildCase]:
    empty = directory / "prior"
    empty.mkdir()
    return [BuildCase("first", base.inputs, empty, _check_first)]


def _cold(base: BasePrior, directory: Path) -> list[BuildCase]:
    inputs = dataclasses.replace(base.inputs, recompute_all=True)
    return [BuildCase("cold", inputs, _prior(base, directory), _check_cold)]


def _warm(base: BasePrior, directory: Path) -> list[BuildCase]:
    return [BuildCase("warm", base.inputs, _prior(base, directory), _check_warm)]


def _incremental_inputs(base: BasePrior, directory: Path) -> BuildInputs:
    inputs_dir = directory / "inputs"
    exemplars = sum(len(entry.get("exemplars", ())) for entry in _ledger_entries(base.inputs.ledger))
    plan = ledger_plan(base.out, base.inputs.ledger, exemplars + 1)
    ledger = incremental_ledger(base, plan, inputs_dir / base.inputs.ledger.name)
    if base.source == "mini":
        audit_path = edited_audit(base.inputs.audit, inputs_dir / base.inputs.audit.name)
    else:
        audit_path = scratch_audit_segment(base.inputs.audit, inputs_dir)
    return dataclasses.replace(base.inputs, audit=audit_path, ledger=ledger)


def _incremental(base: BasePrior, directory: Path) -> list[BuildCase]:
    inputs = _incremental_inputs(base, directory)
    return [BuildCase("incremental", inputs, _prior(base, directory), _check_incremental)]


def _rebase(base: BasePrior, directory: Path) -> list[BuildCase]:
    prior = _prior(base, directory)
    for output in (unit_cache.STORE_NAME, unit_index.INDEX_NAME):
        row_delta.head_path(prior, output).unlink()
    return [BuildCase("rebase", base.inputs, prior, _check_rebase)]


def _restamped(base: BasePrior, directory: Path) -> list[BuildCase]:
    prior = _prior(base, directory)
    move_store_stamp(prior, "units_engine")
    move_signature_stamp(prior, "comparator_code")
    return [BuildCase("restamped", base.inputs, prior, _check_restamped)]


def _rederived(base: BasePrior, directory: Path) -> list[BuildCase]:
    prior = _prior(base, directory)
    move_store_stamp(prior, "spec_structure")
    return [BuildCase("rederived", base.inputs, prior, _check_rederived)]


def _relocate(base: BasePrior, directory: Path) -> list[BuildCase]:
    prior = _prior(base, directory)
    relocate_parts(prior)
    return [BuildCase("relocate", base.inputs, prior, _check_relocate)]


def _torn(base: BasePrior, directory: Path) -> list[BuildCase]:
    inputs = _incremental_inputs(base, directory)
    prior = directory / "prior"
    _required_run(
        run_python_build(inputs, prior, directory / "logs", "incremental", prior=base.out),
        "the torn scenario's incremental build",
    )
    tear(prior, base.out)
    return [BuildCase("torn", inputs, prior, _check_torn)]


def _refusals(base: BasePrior, directory: Path) -> list[BuildCase]:
    """One case a refusal point past the plan, each over its own clone of the base prior. The two that refuse a unit reused from the store run at the harness's default width: a build at one job stamps no `units_engine` line, so it reuses nothing from a store the Rust units engine stamped, and over unchanged inputs neither writes the signature store. The contract check's case rewrites the signature store, since its audit's identity moves, so it runs at one job, where the store is written inline (§ Failures)."""
    stale = _prior(base, directory, "prior-stale-sample")
    stale_id = stale_sample_store(stale)
    unreadable = _prior(base, directory, "prior-unreadable-fragment")
    corrupted_id = corrupt_copied_fragment(unreadable)
    diverging = dataclasses.replace(
        base.inputs, audit=diverging_audit(base.inputs.audit, directory / "inputs" / base.inputs.audit.name)
    )
    return [
        BuildCase("stale-sample", base.inputs, stale, _refused(_STALE_SAMPLE, stale_id)),
        BuildCase(
            "unreadable-fragment", base.inputs, unreadable, _refused(_UNREADABLE_FRAGMENT, corrupted_id)
        ),
        BuildCase(
            "contract-check",
            diverging,
            _prior(base, directory, "prior-contract-check"),
            _refused(
                _CONTRACT_CHECK, "enricher: re-settled cells diverge from the audit in 1 units", written=True
            ),
            jobs=1,
        ),
    ]


@dataclass(frozen=True)
class ChainStep:
    """One step of `CHAIN`: how many builds it runs, the ledger state each reads (`_chain_ledger`), whether its pass's fold claim is held by another output (`HELD_CLAIM_OUTPUT`), and what the store and the units index must write."""

    builds: int
    state: str
    held: bool
    store: str
    index: str


# How many units of the large class one chain build moves its exemplar flags onto, and as many off. Small enough that a step's records, with one fold's address moves, keep sixteen store segments below the store's base, and large enough that the re-patched fragments' dead bytes fold the class within the chain.
CHAIN_BLOCK = 8
# The chain's steps (`rebuild/CORPUS-PARENT-PORT-PLAN.md` § Scenarios, `chain`), each over the build before's output. Ledger states: `alternate` flips the small class's `no_verdict` back and forth and moves the large class's exemplar block to its next `CHAIN_BLOCK` units on every build, changing records of both the store and the index and appending re-patched fragments to the large class until its dead bytes fold it; `exemplars` moves only the exemplar block, which no index line carries; `over-half` puts exemplars on three quarters of the large class, which the share rule writes directly; `most` flips `no_verdict` on every ledger class present, which changes more than half of the store's records and nearly every index line. The store and the index outcomes are `segment`, `head`, or a base's reason. The first builds take both outputs to their cap; the next folds the index, which claims the pass's fold first, while the store writes a segment past its cap; the store then folds on a build that changes no index line; both then take segments under a claim another output holds until `row_delta.fold_forced` folds them; and the last builds write a class directly, write the store's new base by the share rule twice, as most classes flip and flip back, and fold the index once its two segments pass its base.
CHAIN = (
    ChainStep(row_delta.SEGMENT_CAP, "alternate", False, "segment", "segment"),
    ChainStep(1, "alternate", False, "segment", "fold"),
    ChainStep(1, "exemplars", False, "fold", "head"),
    ChainStep(row_delta.SEGMENT_CAP + row_delta.FOLD_OVERRUN, "alternate", True, "segment", "segment"),
    ChainStep(1, "alternate", True, "fold", "fold"),
    ChainStep(1, "over-half", False, "segment", "head"),
    ChainStep(1, "most", False, "share", "segment"),
    ChainStep(1, "alternate", False, "share", "segment"),
    ChainStep(1, "alternate", False, "segment", "fold"),
)


def _chain_ledger(
    base: BasePrior, plan: LedgerPlan, flipped: bool, exemplars: Sequence[tuple[str, str]], most: bool
) -> list[dict]:
    """The base ledger with the small class's `no_verdict` flipped when `flipped`, every present ledger class's flipped when `most`, and `exemplars` added to the large class's own."""
    entries = _ledger_entries(base.inputs.ledger)
    present = {meta["id"] for meta in _manifest_classes(base.out)}
    taken = {(item["config"], item["codepoints"]) for entry in entries for item in entry.get("exemplars", ())}
    for entry in entries:
        if (entry["id"] == plan.small and flipped) or (most and entry["id"] in present):
            _flip_no_verdict(entry)
        if entry["id"] == plan.large:
            own = [(item["config"], item["codepoints"]) for item in entry.get("exemplars", ())]
            _with_exemplars(entry, [*own, *(key for key in exemplars if key not in taken)])
    return entries


def _chain(base: BasePrior, directory: Path) -> list[BuildCase]:
    plan = ledger_plan(base.out, base.inputs.ledger)
    keys = plan.large_keys
    over_half = keys[: 3 * len(keys) // 4]
    cases: list[BuildCase] = []
    flipped = False
    moves = 0
    prior: Path | None = _prior(base, directory)
    for number, step in enumerate(CHAIN):
        for _ in range(step.builds):
            if step.state in ("alternate", "exemplars"):
                flipped = flipped != (step.state == "alternate")
                moves += 1
                block = [keys[(moves * CHAIN_BLOCK + offset) % len(keys)] for offset in range(CHAIN_BLOCK)]
                entries = _chain_ledger(base, plan, flipped, block, False)
            else:
                entries = _chain_ledger(base, plan, flipped, over_half, step.state == "most")
            label = f"{len(cases) + 1:02d}-{step.state}"
            ledger = _write_ledger(directory / "inputs" / f"{label}.yaml", entries, base.inputs.ledger)
            cases.append(
                BuildCase(
                    label,
                    dataclasses.replace(base.inputs, ledger=ledger),
                    prior,
                    _chain_check(step, number),
                    claim_held_by=HELD_CLAIM_OUTPUT if step.held else None,
                )
            )
            prior = None
    return cases


_PREPARERS: dict[str, Callable[[BasePrior, Path], list[BuildCase]]] = {
    "first": _first,
    "cold": _cold,
    "warm": _warm,
    "incremental": _incremental,
    "rebase": _rebase,
    "restamped": _restamped,
    "rederived": _rederived,
    "relocate": _relocate,
    "torn": _torn,
    "chain": _chain,
    "refusals": _refusals,
}


# --- the scenario checks ------------------------------------------------------------------------

_REUSED = r"reused (\d[\d,]*) of (\d[\d,]*) units from the cache"
_KEPT_RECORDS = r"kept (\d[\d,]*) prior records by id"
_RECOMPUTED = r"recomputed=(\d[\d,]*), verified=(\d[\d,]*) cached"
_SIGNATURES = r"\(signatures: (\d[\d,]*) cached, (\d[\d,]*) shaped"
_PRIOR_STORE = r"prior store (\d[\d,]*) hit, (\d[\d,]*) unhit, (\d[\d,]*) missed"
_SIGNATURES_WRITTEN = r"ink-signature store written, (\d[\d,]*) entries"
_BYTE_COPIED = r"byte-copied (\d[\d,]*) of (\d[\d,]*) fragments, copied forward (\d[\d,]*) sidecar rows"
_WRITE_COUNTS = r"kept (\d[\d,]*) by compare, folded (\d[\d,]*) classes, wrote (\d[\d,]*) directly, deflated (\d[\d,]*) fragments"
_SIGNATURES_LEFT = "ink-signature store left as it is"
_STALE_SAMPLE = "the unit cache supplied 1 of "
_UNREADABLE_FRAGMENT = "cannot be read back"
_CONTRACT_CHECK = "contract check failed:"


class _Problems(list):
    """The problems one check finds, each added by `expect` when what it states does not hold."""

    def expect(self, holds: object, problem: str) -> None:
        if not holds:
            self.append(problem)


def _succeeded(run: BuildRun) -> _Problems:
    problems = _Problems()
    problems.expect(run.returncode == 0, f"the build exited {run.returncode}: {run.message()}")
    return problems


def _store_note(run: BuildRun) -> str:
    return run.note("cache")


def _index_note(run: BuildRun) -> str:
    return run.note("manifest+check")


def _units_total(run: BuildRun) -> int:
    found = run.counts("plan", _REUSED)
    return found[1] if found else -1


def _overflow_parts(corpus: Path) -> list[str]:
    return sorted(
        part for meta in _manifest_classes(corpus) for part in unit_index.class_shards(meta) if ".x" in part
    )


def _plain_parts(corpus: Path) -> bool:
    return all(
        (corpus / part).read_bytes()[:1] == b"["
        for meta in _manifest_classes(corpus)
        for part in unit_index.class_shards(meta)
    )


def _check_first(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    problems.expect(
        run.counts("plan", _REUSED) == (0, _units_total(run)),
        "the plan reused a unit from an empty directory",
    )
    problems.expect(
        not _overflow_parts(run.out) and _plain_parts(run.out),
        "the first build wrote a part that is not a plain base",
    )
    problems.expect("(store: a new base (first)" in _store_note(run), "the store is not a new base (first)")
    problems.expect("(index: a new base (first)" in _index_note(run), "the index is not a new base (first)")
    signatures = run.counts("load", _SIGNATURES)
    written = run.counts("units", _SIGNATURES_WRITTEN)
    problems.expect(
        signatures is not None and signatures[0] == 0 and signatures[1] > 0 and written == (signatures[1],),
        "not every signature was shaped and written",
    )
    return problems


def _check_cold(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    problems.expect(
        run.counts("plan", _REUSED) == (0, total), "the plan reused a unit under --recompute-all-units"
    )
    problems.expect(run.counts("units", _RECOMPUTED) == (total, 0), "not every unit was recomputed")
    signatures = run.counts("load", _SIGNATURES)
    problems.expect(
        signatures is not None and signatures[0] == 0 and signatures[1] > 0,
        "the load did not shape every sibling window",
    )
    problems.expect("key-only read ran" in run.note("load"), "the key-only read did not run")
    problems.expect(
        run.counts("units", _SIGNATURES_WRITTEN) is not None, "the signature store was not written"
    )
    written = run.counts("manifest+check", _WRITE_COUNTS)
    problems.expect(written is not None and written[0] == total, "not every unit was kept by compare")
    problems.expect("(store: head only)" in _store_note(run), "the store's lines did not all compare equal")
    problems.expect("(index: head only)" in _index_note(run), "the index's lines did not all compare equal")
    return problems


def _check_warm(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    problems.expect(run.counts("plan", _REUSED) == (total, total), "not every unit was reused")
    problems.expect(
        run.counts("manifest+check", _BYTE_COPIED) == (total, total, total),
        "not every fragment was byte-copied and every sidecar row copied forward",
    )
    problems.expect("(store: head only)" in _store_note(run), "the store wrote more than its head")
    problems.expect("(index: head only)" in _index_note(run), "the index wrote more than its head")
    problems.expect(_SIGNATURES_LEFT in run.note("units"), "the signature store was rewritten")
    problems.expect(
        (run.out / "manifest.json").read_bytes() == (run.prior / "manifest.json").read_bytes(),
        "manifest.json differs from the prior's",
    )
    return problems


def _segment_rows(corpus: Path, output: str) -> list[row_delta.Row]:
    """The rows of the last segment the output's head lists, or none when it lists no segment."""
    head = row_delta.read_head(corpus, output)
    if head is None or not head.segments:
        return []
    number = len(head.segments)
    with (corpus / head.segments[-1].name).open("rb") as handle:
        if head.merge == row_delta.MERGE_KEY:
            return [
                row_delta.Row(op, (0, ""), payload)
                for op, payload in row_delta.keyed_segment_rows(handle, output, head.generation, number)
            ]
        return list(row_delta.segment_rows(handle, output, head.generation, number))


def _check_incremental(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    reused = run.counts("plan", _REUSED)
    problems.expect(
        reused is not None and 0 < reused[0] < total, "the plan did not reuse some units and recompute others"
    )
    problems.expect("key-only read ran" in run.note("load"), "the key-only read did not run")
    prior_store = run.counts("load", _PRIOR_STORE)
    problems.expect(
        prior_store is not None and all(prior_store),
        "the key-only read did not hit, leave unhit and miss prior signatures",
    )
    problems.expect(
        prior_store is not None
        and run.counts("units", _SIGNATURES_WRITTEN) == (prior_store[0] + prior_store[2],),
        "the signature store was not written as the prior hits and the misses",
    )
    written = run.counts("manifest+check", _WRITE_COUNTS)
    problems.expect(written is not None and written[2] > 0, "no class was written directly")
    copied = run.counts("manifest+check", _BYTE_COPIED)
    problems.expect(
        copied is not None and 0 < copied[2] < copied[1], "copy-forward did not run beside changed rows"
    )
    problems.expect("(store: a segment" in _store_note(run), "the store did not write a segment")
    problems.expect("(index: a segment" in _index_note(run), "the index did not write a segment")
    for output in (unit_cache.STORE_NAME, unit_index.INDEX_NAME):
        problems.expect(
            any(row.op == row_delta.TOMBSTONE for row in _segment_rows(run.out, output)),
            f"the {output} segment holds no tombstone for the removed windows",
        )
    problems.expect(
        set(_overflow_parts(run.out)) - set(_overflow_parts(run.prior)) or _appended(run),
        "nothing was appended to an overflow part",
    )
    return problems


def _appended(run: BuildRun) -> bool:
    found = re.search(r"appended (\d+\.\d) MB", run.note("manifest+check"))
    return found is not None and float(found.group(1)) > 0


def _gzip_members(path: Path) -> tuple[int, int]:
    """How many gzip members a file holds, and their inflated bytes in all."""
    data = path.read_bytes()
    members = inflated = 0
    while data:
        stream = zlib.decompressobj(wbits=31)
        inflated += len(stream.decompress(data)) + len(stream.flush())
        members += 1
        data = stream.unused_data
    return members, inflated


def _check_rebase(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    problems.expect(
        run.says(f"unit cache: {unit_cache.NO_STORE_NOTE}"), "the build did not say it found no unit store"
    )
    problems.expect(run.counts("plan", _REUSED) == (0, total), "the plan reused a unit with no store")
    problems.expect(run.counts("units", _RECOMPUTED) == (total, 0), "not every unit was recomputed")
    problems.expect("(store: a new base (first)" in _store_note(run), "the store is not a new base")
    problems.expect(
        "(index: a new base (first)" in _index_note(run), "the index is not a new base by its first route"
    )
    written = run.counts("manifest+check", _WRITE_COUNTS)
    classes = len(_manifest_classes(run.out))
    problems.expect(
        written is not None and written[2] == classes and written[3] == total,
        "the shards were not every class written directly, every fragment deflated",
    )
    store_head = row_delta.read_head(run.out, unit_cache.STORE_NAME)
    listed = set() if store_head is None else {part.name for part in store_head.parts}
    stray = [
        path.name
        for path in run.out.iterdir()
        if path.name.startswith(unit_cache.STORE_NAME + ".")
        and path.name != row_delta.head_name(unit_cache.STORE_NAME)
        and path.name not in listed
    ]
    problems.expect(not stray, f"the store's unlisted parts were not removed: {stray}")
    index_head = row_delta.read_head(run.out, unit_index.INDEX_NAME)
    if index_head is None:
        problems.append("the index has no head")
    else:
        members, inflated = _gzip_members(run.out / index_head.base.name)
        problems.expect(
            members == -(-inflated // unit_index.INDEX_MEMBER_BYTES),
            f"the index's new base holds {members} members, not one each {unit_index.INDEX_MEMBER_BYTES} plain bytes",
        )
    return problems


def _check_restamped(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    warns = run.warns()
    problems.expect(
        any(line.startswith("unit cache: the stamp moved at units_engine") for line in warns),
        "the unit store's stale-stamp note did not name units_engine",
    )
    problems.expect(
        any(line.startswith("ink-signature store: the stamp moved at comparator_code") for line in warns),
        "the ink-signature store's stale-stamp note did not name comparator_code",
    )
    problems.expect(run.counts("plan", _REUSED) == (0, total), "the plan parsed a store record")
    problems.expect(run.counts("units", _RECOMPUTED) == (total, 0), "not every unit was recomputed")
    signatures = run.counts("load", _SIGNATURES)
    problems.expect(
        signatures is not None and signatures[0] == 0 and signatures[1] > 0, "not every window was reshaped"
    )
    problems.expect("(store: head only)" in _store_note(run), "the store wrote more than its head")
    return problems


def _check_rederived(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    problems.expect(
        any(line.startswith("unit cache: the stamp moved at spec_structure") for line in run.warns()),
        "the unit store's note did not name spec_structure",
    )
    problems.expect(
        "(store: a new base (rederived)" in _store_note(run), "the store is not a new base (rederived)"
    )
    return problems


def _check_relocate(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    problems.expect(run.counts("plan", _REUSED) == (total, total), "the walk did not place every unit")
    written = run.counts("manifest+check", _WRITE_COUNTS)
    classes = len(_manifest_classes(run.out))
    problems.expect(written is not None and written[2] == classes, "not every class was written directly")
    return problems


def _check_torn(run: BuildRun) -> list[str]:
    problems = _succeeded(run)
    total = _units_total(run)
    problems.expect(
        any(line.startswith("unit cache: the manifest") for line in run.warns()),
        "the unit store's note did not name the moved manifest",
    )
    problems.expect(run.counts("plan", _REUSED) == (0, total), "the plan parsed a store record")
    problems.expect(run.counts("plan", _KEPT_RECORDS) == (0,), "the plan kept a prior record by id")
    return problems


def _refused(refusal: str, detail: str, *, written: bool = False) -> BuildCheck:
    """A check that the build exited 1 with `refusal` naming `detail`, and, when `written`, after writing its signature store."""

    def check(run: BuildRun) -> list[str]:
        problems = _Problems()
        problems.expect(run.returncode == 1, f"the build exited {run.returncode}, not 1")
        problems.expect(
            refusal in run.log and detail in run.log,
            f"the build did not refuse with {refusal!r} naming {detail!r}",
        )
        if written:
            problems.expect(
                run.counts("units", _SIGNATURES_WRITTEN) is not None, "the signature store was not rewritten"
            )
        return problems

    return check


def _outcome(note: str, outcome: str) -> bool:
    if outcome == "segment":
        return "a segment of " in note
    if outcome == "head":
        return "head only" in note
    return f"a new base ({outcome})" in note


def _chain_check(step: ChainStep, number: int) -> BuildCheck:
    def check(run: BuildRun) -> list[str]:
        problems = _succeeded(run)
        problems.expect(
            _outcome(_store_note(run), step.store), f"step {number}: the store did not write {step.store}"
        )
        problems.expect(
            _outcome(_index_note(run), step.index), f"step {number}: the index did not write {step.index}"
        )
        for output, outcome in ((unit_cache.STORE_NAME, step.store), (unit_index.INDEX_NAME, step.index)):
            head = row_delta.read_head(run.prior, output)
            assert head is not None
            if outcome == "fold":
                forced = row_delta.fold_forced(head)
                problems.expect(row_delta.fold_due(head), f"step {number}: {output}'s fold was not due")
                problems.expect(
                    forced == step.held,
                    f"step {number}: {output}'s fold was {'' if forced else 'not '}forced",
                )
        if step.held:
            assert run.cycle_var is not None
            claim = json.loads((run.cycle_var / cycle_paths.FOLD_CLAIM_NAME).read_text(encoding="utf-8"))
            problems.expect(
                claim.get("output") == HELD_CLAIM_OUTPUT,
                f"step {number}: the build took the claim another output held",
            )
        if step.state == "over-half":
            written = run.counts("manifest+check", _WRITE_COUNTS)
            problems.expect(
                written is not None and written[2] > 0, f"step {number}: no class was written directly"
            )
        return problems

    return check


def check_chain(runs: Sequence[BuildRun]) -> list[str]:
    """What the chain must reach across its builds beyond each step's own check: a class folded and a fragment appended."""
    problems = _Problems()
    problems.expect(
        any((found := run.counts("manifest+check", _WRITE_COUNTS)) and found[1] > 0 for run in runs),
        "no build folded a class's shards",
    )
    problems.expect(any(_overflow_parts(run.out) for run in runs), "no build appended to an overflow part")
    return problems


def prepare(source: str, scenarios: Sequence[str], check: bool) -> bool:
    """Prepare each named scenario, and with `check` run its Python builds into clones under its directory's `check/` and report whether each reached what its row names (`BuildCase.check`). Returns whether every check passed."""
    ok = True
    for name in scenarios:
        scenario = open_build_source(source, name)
        print(
            f"{source}-{name}: prepared {len(scenario.cases)} builds under {scenario.directory}",
            file=sys.stderr,
        )
        if not check:
            continue
        runs: list[BuildRun] = []
        previous: Path | None = None
        for index, case in enumerate(scenario.cases):
            prior = case.prior if case.prior is not None else previous
            assert prior is not None, "a scenario's first build names its prior"
            out = scenario.directory / "check" / case.name
            run = run_python_build(
                case.inputs,
                out,
                scenario.directory / "check" / "logs",
                case.name,
                prior=prior,
                jobs=case.jobs,
                claim_held_by=case.claim_held_by,
            )
            runs.append(run)
            problems = case.check(run)
            ok = ok and not problems
            status = "reached its row" if not problems else "; ".join(problems)
            print(f"{source}-{name} {case.name}: {status}", file=sys.stderr)
            previous = out
            del index
        if name == "chain":
            problems = check_chain(runs)
            ok = ok and not problems
            print(
                f"{source}-{name}: {'reached its row' if not problems else '; '.join(problems)}",
                file=sys.stderr,
            )
    return ok


def scenario_names(source: str, scenario: str) -> list[str]:
    """The scenarios `--scenario` names on a source: one by name, or `all`, every scenario of the source but those that run by name only."""
    if scenario == "all":
        return [
            name
            for name, sources in BUILD_SCENARIOS.items()
            if source in sources and name not in BY_NAME_ONLY
        ]
    return [scenario]


# --- the build's stages -------------------------------------------------------------------------

BUILD_SOURCE_FORMAT = "ams-review-build-stage-source/1"
# The stride of the units a live dump's boundary stages record a line for, unless `--every` names another; a mini dump records every unit.
LIVE_EVERY = 1000
# Where a build dump keeps its clones and the units init, under its directory.
DUMP_PRIOR = "prior"
DUMP_INPUTS = "inputs"
DUMP_INIT = "init"
# Where a build dump runs its build, under its directory, at one path so that every dump into the directory hands the build the same values.
DUMP_SCRATCH = "build-stage-dump"
# The Rust build's input for the dumped run's values, written without descriptors, beside `source.json`.
BUILD_INPUT_FILE = "build.json"
BUILD_INPUT_STAGE = "build-input"
# The calls on each of `columns`' collections the `columns` stage records, by class: a followed collection's from its making (`__init__`), and a window index's on its state at each call.
COLUMNS_CALLS = {
    "StringTable": ("__init__", "id", "find", "optional", "__getitem__", "__len__"),
    "TuplePool": ("__init__", "id", "pooled", "seal", "__getitem__", "__len__"),
    "MappingPool": ("__init__", "id", "pooled", "__getitem__", "__len__"),
    "KeyIndex": ("__init__", "find", "nbytes"),
    "WindowIndex": ("candidates", "key", "runs", "remapped", "nbytes"),
}
# The build's calls a build stage can record, by name, as `HOOKS` names the worker's: each is the module and the attribute path the wrapper replaces for the length of a build. The task that adds a stage adds the hook points it records.
BUILD_HOOKS: dict[str, tuple[str, str]] = {
    "output_sink.write_output": ("rebuild.pipeline.output_sink", "write_output"),
    "output_sink.replace_output": ("rebuild.pipeline.output_sink", "replace_output"),
    "output_sink.note_rewritten": ("rebuild.pipeline.output_sink", "note_rewritten"),
    "gzip.GzipFile.__init__": ("gzip", "GzipFile.__init__"),
    "gzip.GzipFile.write": ("gzip", "GzipFile.write"),
    "gzip.GzipFile.close": ("gzip", "GzipFile.close"),
    "gzip.compress": ("gzip", "compress"),
    "unit_index._Members.__init__": ("rebuild.review.unit_index", "_Members.__init__"),
    "unit_index._Members.write": ("rebuild.review.unit_index", "_Members.write"),
    "unit_index._Members.__exit__": ("rebuild.review.unit_index", "_Members.__exit__"),
    "unit_index.deflate_fragment": ("rebuild.review.unit_index", "deflate_fragment"),
    "unit_index.inflate_fragment": ("rebuild.review.unit_index", "inflate_fragment"),
    "json.dumps": ("json", "dumps"),
    "json.loads": ("json", "loads"),
    "build._verification_sample": ("rebuild.review.build", "_verification_sample"),
    "row_delta.read_head": ("rebuild.pipeline.row_delta", "read_head"),
    "row_delta.open_parts": ("rebuild.pipeline.row_delta", "open_parts"),
    "row_delta.check_segment_header": ("rebuild.pipeline.row_delta", "check_segment_header"),
    **{
        f"row_delta.{name}": ("rebuild.pipeline.row_delta", name)
        for name in (
            "places_between",
            "row_line",
            "row_length",
            "segment_header",
            "write_part",
            "write_segment",
            "StreamedPart.close",
            "commit",
            "remove_unlisted",
            "remove_staging",
            "writes_base",
            "fold_due",
            "fold_forced",
            "claim_fold",
            "with_segment",
        )
    },
    "build.audit_layout": ("rebuild.review.build", "audit_layout"),
    "build.stream_workload": ("rebuild.review.build", "stream_workload"),
    "audit_stream.window_ranges": ("rebuild.review.audit_stream", "window_ranges"),
    **{
        f"unit_cache.SignatureArrays.{name}": ("rebuild.review.unit_cache", f"SignatureArrays.{name}")
        for name in (
            "__init__",
            "load",
            "stamps_hold",
            "prior_index",
            "mark_hit",
            "digest_of",
            "set_misses",
            "add_late",
            "hits",
            "misses",
            "unhit",
            "unchanged",
        )
    },
    "unit_cache.write_signature_arrays": ("rebuild.review.unit_cache", "write_signature_arrays"),
    "unit_cache.signature_miss_note": ("rebuild.review.unit_cache", "signature_miss_note"),
    "build._shape_misses": ("rebuild.review.build", "_shape_misses"),
    "build._LateShaper.__call__": ("rebuild.review.build", "_LateShaper.__call__"),
    "build._find_misses": ("rebuild.review.build", "_find_misses"),
    "build._shape_found_misses": ("rebuild.review.build", "_shape_found_misses"),
    "build._LateShaper.release": ("rebuild.review.build", "_LateShaper.release"),
    **{
        f"columns.{owner}.{name}": ("rebuild.review.columns", f"{owner}.{name}")
        for owner, names in COLUMNS_CALLS.items()
        for name in names
    },
    "columns.window_word": ("rebuild.review.columns", "window_word"),
    "audit.window_word": ("rebuild.review.audit", "window_word"),
    "unit_cache.sorted_by_word": ("rebuild.review.unit_cache", "sorted_by_word"),
    "unit_store.sorted_by_word": ("rebuild.review.unit_store", "sorted_by_word"),
    "audit._counting_permutation": ("rebuild.review.audit", "_counting_permutation"),
    "audit._triage_permutation": ("rebuild.review.audit", "_triage_permutation"),
    "audit.family_ranks": ("rebuild.review.audit", "family_ranks"),
    "build.family_ranks": ("rebuild.review.build", "family_ranks"),
    "build.triage_key": ("rebuild.review.build", "triage_key"),
    "audit._merge_members": ("rebuild.review.audit", "_merge_members"),
    "facts.PremergeSnapshot.rebase": ("rebuild.review.facts", "PremergeSnapshot.rebase"),
    "build._load_corpus": ("rebuild.review.build", "_load_corpus"),
    **{
        f"unit_cache.{name}": ("rebuild.review.unit_cache", name)
        for name in (
            "_parsed_cached_unit",
            "record_line",
            "prior_record_or_none",
            "named_ordinal",
            "id_word",
            "primary_units_crc",
            "is_content_id",
            "open_store",
            "scan_store",
            "scan_marked",
            "locate_prior_fragments",
            "store_miss_note",
            "rederiving_moved",
            "adopt_single_file",
        )
    },
    "unit_cache.LocatedFragments.find": ("rebuild.review.unit_cache", "LocatedFragments.find"),
    **{
        f"unit_store.UnitStore.{method}": ("rebuild.review.unit_store", f"UnitStore.{method}")
        for method in (
            "__init__",
            "load_projection",
            "load_cached",
            "add_prior_record",
            "seal_prior_records",
            "set_prior_line",
            "input_key_index",
            "set_primary_units",
            "set_written_span",
            "set_written_part",
            "set_policy_file",
            "set_config_note",
        )
    },
    "build._phase_timing": ("rebuild.review.build", "_phase_timing"),
    "build._RecomputeRunner.phase1": ("rebuild.review.build", "_RecomputeRunner.phase1"),
    "build.assign_batches": ("rebuild.review.build", "assign_batches"),
    "build._write_runs": ("rebuild.review.build", "_write_runs"),
    "build._ShardWriter.open": ("rebuild.review.build", "_ShardWriter.open"),
    "build._ShardWriter.holds": ("rebuild.review.build", "_ShardWriter.holds"),
    "build._ShardWriter.keep": ("rebuild.review.build", "_ShardWriter.keep"),
    "build._ShardWriter.add_bytes": ("rebuild.review.build", "_ShardWriter.add_bytes"),
    "build._ShardWriter.close": ("rebuild.review.build", "_ShardWriter.close"),
    **{
        f"unit_cache.PriorFragmentReader.{name}": ("rebuild.review.unit_cache", f"PriorFragmentReader.{name}")
        for name in ("read", "read_bytes", "same_bytes")
    },
    "build._FragmentArena.read": ("rebuild.review.build", "_FragmentArena.read"),
    "build._emission": ("rebuild.review.build", "_emission"),
    "build.check_unit": ("rebuild.review.build", "check_unit"),
    "build._CorpusCheck.class_start": ("rebuild.review.build", "_CorpusCheck.class_start"),
    "build._CorpusCheck.unit": ("rebuild.review.build", "_CorpusCheck.unit"),
    "build._CorpusCheck.class_end": ("rebuild.review.build", "_CorpusCheck.class_end"),
    "build._CorpusCheck.take_facts": ("rebuild.review.build", "_CorpusCheck.take_facts"),
    "build._CorpusCheck.absorb": ("rebuild.review.build", "_CorpusCheck.absorb"),
    "build._CorpusCheck.finish": ("rebuild.review.build", "_CorpusCheck.finish"),
    **{
        f"build._SidecarLines.{name}": ("rebuild.review.build", f"_SidecarLines.{name}")
        for name in ("__init__", "unit", "cached", "close")
    },
    "app_index.LocatorBlocks.add": ("rebuild.review.app_index", "LocatorBlocks.add"),
    "unit_index.LineCursor.take": ("rebuild.review.unit_index", "LineCursor.take"),
    "build._write_json": ("rebuild.review.build", "_write_json"),
    "build._prune_orphan_shards": ("rebuild.review.build", "_prune_orphan_shards"),
    "unit_index.write_index_lines": ("rebuild.review.unit_index", "write_index_lines"),
}


@dataclass(frozen=True)
class BuildSource:
    """A build dump's source: its scenario, prepared over the source's base prior (`open_build_source`), the scenario's one build, the dump's directory, the stride of the units a boundary stage records a line for, and the widths the build runs at (`build_widths`)."""

    scenario: BuildScenario
    case: BuildCase
    out: Path
    every: int
    jobs: int
    signature_jobs: int

    def describe(self, inputs: Mapping[str, str], init: Path) -> dict:
        return {
            "format": BUILD_SOURCE_FORMAT,
            "run": "build",
            "source": self.scenario.source,
            "scenario": self.scenario.name,
            "case": self.case.name,
            "every": self.every,
            "widths": {"jobs": self.jobs, "signature_jobs": self.signature_jobs, "write_jobs": 1},
            "read": self.case.inputs.request(),
            "prior": DUMP_PRIOR,
            "inputs": dict(inputs),
            "init": init.relative_to(self.out).as_posix(),
        }


SINK_STAGE = "sink"
# The `sink` stage's directory, beside its stage file, of the bytes its lines name by sha256: what a target held, what a call writes and the source it copies.
SINK_BLOBS = "sink.blobs"
# The `output_sink` calls the `sink` stage records, by the name `BUILD_HOOKS` gives each, with the signature that binds a hooked call's arguments.
_SINK_SIGNATURES = {
    f"output_sink.{name}": inspect.signature(getattr(output_sink, name))
    for name in ("write_output", "replace_output", "note_rewritten")
}
# The blob directory of the build whose `sink` stage records, for the length of that build (`_sink_blobs_at`).
_SINK_BLOBS_AT: list[Path] = []
# A generated payload past one chunk of `output_sink`'s compare (`output_sink._CHUNK`).
SINK_LARGE_BYTES = 300_000


@contextlib.contextmanager
def _sink_blobs_at(directory: Path) -> Iterator[None]:
    _SINK_BLOBS_AT.append(directory)
    try:
        yield
    finally:
        _SINK_BLOBS_AT.pop()


def _blob_into(directory: Path, data: bytes) -> str:
    """Keep `data` under `directory` named by its sha256, once, and return the digest."""
    digest = hashlib.sha256(data).hexdigest()
    path = directory / digest
    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return digest


def _sink_held(target: Path, blobs: Path) -> tuple[tuple, int | None]:
    """What `target` holds before a call, as a `sink` line's `held` names it, with a file's bytes kept in `blobs`: `("missing",)`, `("absent",)` when its directory is missing too, `("file", sha256)`, `("fifo",)`, `("directory",)`, `("link", "null")` for a link to the null device or `("link", "file", sha256)`; and its inode, None when there is nothing there."""
    try:
        found = os.lstat(target)
    except FileNotFoundError:
        return ("missing",) if target.parent.is_dir() else ("absent",), None
    mode = found.st_mode
    if stat.S_ISLNK(mode):
        if os.readlink(target) == os.devnull:
            held: tuple = ("link", "null")
        else:
            held = ("link", "file", _blob_into(blobs, target.read_bytes()))
    elif stat.S_ISREG(mode):
        held = ("file", _blob_into(blobs, target.read_bytes()))
    elif stat.S_ISFIFO(mode):
        held = ("fifo",)
    elif stat.S_ISDIR(mode):
        held = ("directory",)
    else:
        raise ValueError(f"the sink stage cannot set {target} up again")
    return held, found.st_ino


def _sink_state(target: Path, held: int | None) -> tuple:
    """What a call left at `target`, by `lstat`: its kind, a file's sha256 (through a link to one), and whether it is still the inode `held` (None when the call found nothing there)."""
    try:
        found = os.lstat(target)
    except FileNotFoundError:
        return ("missing",)
    kept = None if held is None else found.st_ino == held
    mode = found.st_mode
    if stat.S_ISLNK(mode):
        if os.readlink(target) == os.devnull:
            return ("link", "null", kept)
        return ("link", "file", hashlib.sha256(target.read_bytes()).hexdigest(), kept)
    if stat.S_ISREG(mode):
        return ("file", hashlib.sha256(target.read_bytes()).hexdigest(), kept)
    if stat.S_ISFIFO(mode):
        return ("fifo", kept)
    if stat.S_ISDIR(mode):
        return ("directory", kept)
    return ("other", kept)


def _counts_state(path: str | None) -> tuple[str, bytes | None]:
    """The counts file a call finds, as a `sink` line's `counts_file` names it (`none` when no file is named, `missing`, `present`), and what it holds."""
    if not path:
        return "none", None
    try:
        return "present", Path(path).read_bytes()
    except FileNotFoundError:
        return "missing", None


def _counts_appended(path: str | None, counted: bytes | None) -> str | None:
    """What a call appended to the counts file it found holding `counted`, or None when no counts file is there after it."""
    if not path:
        return None
    try:
        after = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    before = counted or b""
    if not after.startswith(before):
        raise ValueError(f"the counts file {path} changed other than by appending")
    return after[len(before) :].decode()


def _sink_line(
    origin: str,
    inputs: Mapping[str, object],
    target: Path | None,
    held: int | None,
    counts: str | None,
    counted: bytes | None,
    returned: object,
) -> dict[str, object]:
    """One `sink` line, keyed by its origin and a digest of its inputs: the inputs, and as its outputs what the call `returned`, what it appended to the counts file (`counts`) and the target's state after it with whether a staging file is left (`after`, None for `note_rewritten`)."""
    after = (
        None
        if target is None
        else (
            _sink_state(target, held),
            os.path.lexists(target.with_name(target.name + output_sink.STAGING_SUFFIX)),
        )
    )
    digest = hashlib.sha1(compact(encode(dict(inputs))).encode("ascii")).hexdigest()[:20]
    return {
        "key": f"{origin}:{inputs['call']}:{digest}",
        **inputs,
        "returned": returned,
        "counts": _counts_appended(counts, counted),
        "after": after,
    }


def _sink_inputs(
    call: str,
    target: Path | None,
    held: tuple | None,
    payload: tuple,
    sha256: str | None,
    keep_if_equal: bool,
    counts_file: str,
) -> dict[str, object]:
    """A `sink` line's inputs: the call, the target's name, what it held, the payload as its kind and the sha256 of its bytes (or `("output", name)` for `note_rewritten`), the `sha256` a writer is kept against, `keep_if_equal`, and the counts file's state."""
    return {
        "call": call,
        "name": None if target is None else target.name,
        "held": held,
        "payload": payload,
        "sha256": sha256,
        "keep_if_equal": keep_if_equal,
        "counts_file": counts_file,
    }


def _sink_call(hook: str) -> Recorder:
    """The `sink` recorder of one `output_sink` call: what the target and the counts file held before it, the bytes it writes (a writer's read from the target once it has run, or, when the call kept the target, the bytes it kept), and what it returned and left."""
    signature = _SINK_SIGNATURES[hook]
    call = hook.removeprefix("output_sink.")

    def record(args: tuple, kwargs: dict) -> Finisher:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = bound.arguments
        blobs = _SINK_BLOBS_AT[-1]
        counts = os.environ.get(output_sink.OUTPUT_COUNTS_ENV)
        counts_file, counted = _counts_state(counts)
        if call == "note_rewritten":
            inputs = _sink_inputs(call, None, None, ("output", arguments["output"]), None, False, counts_file)
            return lambda result: _sink_line("run", inputs, None, None, counts, counted, result)
        target = Path(arguments["target"])
        held, inode = _sink_held(target, blobs)
        data, source = arguments["data"], arguments.get("source")
        if data is not None:
            raw = data.encode("utf-8") if isinstance(data, str) else data
            payload: tuple | None = ("str" if isinstance(data, str) else "bytes", _blob_into(blobs, raw))
        elif source is not None:
            payload = ("source", _blob_into(blobs, Path(source).read_bytes()))
        else:
            payload = None

        def finish(result: object) -> dict[str, object]:
            written = payload
            if written is None:
                written = ("writer", _blob_into(blobs, target.read_bytes()) if result else held[-1])
            inputs = _sink_inputs(
                call,
                target,
                held,
                written,
                arguments.get("sha256"),
                arguments["keep_if_equal"],
                counts_file,
            )
            return _sink_line("run", inputs, target, inode, counts, counted, result)

        return finish

    return record


class _Refused(Exception):
    """What a generated `sink` case's writer or block raises after it has written, to leave the target as it was."""


@dataclass(frozen=True)
class _SinkCase:
    """One generated `sink` case: the call and its payload's kind and value, the target's state before it (`_sink_target`), `keep_if_equal`, the `sha256` a writer is kept against, and the counts file's state; `note_rewritten`'s `output` in place of a payload."""

    call: str
    kind: str
    payload: bytes | str = b""
    held: str = "missing"
    held_bytes: bytes = b""
    keep: bool = False
    sha256: str | None = None
    counts: str = "present"


def _sink_helds(payload: bytes) -> list[tuple[str, str, bytes]]:
    """Each target state a generated case sets up against `payload`, with a label: missing, in a missing directory, the payload's bytes, other bytes of every size relation to them, a FIFO, a directory, a link to the null device and links to a file of the payload's bytes or others."""
    helds = [
        ("missing", "missing", b""),
        ("absent", "absent", b""),
        ("equal", "file", payload),
        ("longer", "file", payload + b"x"),
        ("fifo", "fifo", b""),
        ("directory", "directory", b""),
        ("link-null", "link-null", b""),
        ("link-equal", "link-file", payload),
        ("link-other", "link-file", payload + b"x"),
    ]
    if payload:
        helds += [
            ("shorter", "file", payload[:-1]),
            ("differs-last", "file", payload[:-1] + bytes([payload[-1] ^ 1])),
            ("differs-first", "file", bytes([payload[0] ^ 1]) + payload[1:]),
        ]
    return helds


def _sink_cases() -> Iterator[_SinkCase]:
    """The generated `sink` cases: each call and payload kind over every target state of `_sink_helds`, with and without `keep_if_equal` (but `replacing`, which never compares, and `write_output` over a FIFO, which blocks with no reader), a writer kept against a digest the target does not have, each counts file state, and `note_rewritten`."""
    large = bytes((index * 31 + index // 251) % 256 for index in range(SINK_LARGE_BYTES))
    payloads = {"empty": b"", "small": b"head\n", "large": large}
    calls: list[tuple[str, str, list[bytes | str]]] = [
        ("write_output", "bytes", list(payloads.values())),
        ("write_output", "str", ["\xe9 \xfcn\xefcode ·Pea\n"]),
        ("replace_output", "bytes", list(payloads.values())),
        ("replace_output", "str", ["\xe9 \xfcn\xefcode ·Pea\n"]),
        ("replace_output", "source", list(payloads.values())),
        ("replace_output", "writer", list(payloads.values())),
        ("replace_output", "writer-raises", [payloads["small"]]),
        ("replacing", "stream", list(payloads.values())),
        ("replacing", "stream-raises", [payloads["small"]]),
    ]
    for call, kind, values in calls:
        for value in values:
            raw = value.encode("utf-8") if isinstance(value, str) else value
            for _label, held, held_bytes in _sink_helds(raw):
                if call == "write_output" and held == "fifo":
                    continue
                for keep in (False,) if call == "replacing" else (False, True):
                    sha256 = hashlib.sha256(raw).hexdigest() if keep and kind.startswith("writer") else None
                    yield _SinkCase(call, kind, value, held, held_bytes, keep, sha256)
    small = payloads["small"]
    for held in ("equal", "longer"):
        yield _SinkCase(
            "replace_output",
            "writer",
            small,
            "file",
            small if held == "equal" else small + b"x",
            True,
            hashlib.sha256(b"other").hexdigest(),
        )
    for counts in ("none", "missing"):
        for call, kind, keep in (
            ("write_output", "bytes", True),
            ("replace_output", "bytes", True),
            ("replace_output", "writer", True),
            ("replacing", "stream", False),
        ):
            for held, held_bytes in (("missing", b""), ("file", small)):
                sha256 = hashlib.sha256(small).hexdigest() if kind == "writer" else None
                yield _SinkCase(call, kind, small, held, held_bytes, keep, sha256, counts)
    for counts in ("present", "none", "missing"):
        for output in ("unit-cache.ndjson.gz", "units-index.ndjson.gz"):
            yield _SinkCase("note_rewritten", "output", output, counts=counts)


def _sink_target(directory: Path, case: _SinkCase) -> Path:
    """Set the case's target up under `directory` as its `held` names: nothing, nothing in a missing directory, a file, a FIFO, a directory, or a link to the null device or to a sibling file."""
    directory.mkdir(parents=True)
    name = "target.out"
    if case.held == "absent":
        return directory / "absent" / name
    target = directory / name
    if case.held == "file":
        target.write_bytes(case.held_bytes)
    elif case.held == "fifo":
        os.mkfifo(target)
    elif case.held == "directory":
        target.mkdir()
    elif case.held == "link-null":
        target.symlink_to(os.devnull)
    elif case.held == "link-file":
        (directory / f"{name}.linked").write_bytes(case.held_bytes)
        target.symlink_to(f"{name}.linked")
    return target


@contextlib.contextmanager
def _counts_named(path: Path | None) -> Iterator[None]:
    """Name `path` as the counts file (`output_sink.OUTPUT_COUNTS_ENV`), or none, for the length of the block."""
    held = os.environ.pop(output_sink.OUTPUT_COUNTS_ENV, None)
    if path is not None:
        os.environ[output_sink.OUTPUT_COUNTS_ENV] = str(path)
    try:
        yield
    finally:
        os.environ.pop(output_sink.OUTPUT_COUNTS_ENV, None)
        if held is not None:
            os.environ[output_sink.OUTPUT_COUNTS_ENV] = held


def _sink_make(case: _SinkCase, target: Path, source: Path) -> object:
    """Make the case's call over `target` and return what it returned."""
    raw = case.payload.encode("utf-8") if isinstance(case.payload, str) else case.payload
    if case.call == "write_output":
        return output_sink.write_output(target, case.payload, keep_if_equal=case.keep)
    if case.call == "replacing":
        with output_sink.replacing(target) as handle:
            handle.write(raw)
            if case.kind == "stream-raises":
                raise _Refused
        return None
    if case.kind == "source":
        source.write_bytes(raw)
        return output_sink.replace_output(target, source=source, keep_if_equal=case.keep)
    if case.kind.startswith("writer"):

        def fill(staging: Path) -> None:
            staging.write_bytes(raw)
            if case.kind == "writer-raises":
                raise _Refused

        return output_sink.replace_output(target, writer=fill, sha256=case.sha256, keep_if_equal=case.keep)
    return output_sink.replace_output(target, case.payload, keep_if_equal=case.keep)


def _sink_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `sink` stage's generated lines (`_sink_cases`), each made through `output_sink` over a target set up in a directory of its own, with its bytes kept in the dump's blob directory."""
    blobs = source.out / SINK_BLOBS
    with tempfile.TemporaryDirectory(prefix="sink-generated-") as held:
        scratch = Path(held)
        for index, case in enumerate(_sink_cases()):
            counts = None if case.counts == "none" else scratch / f"{index}.counts"
            if case.counts == "present":
                assert counts is not None
                counts.write_bytes(b"")
            with _counts_named(counts):
                counts_file, counted = _counts_state(None if counts is None else str(counts))
                if case.call == "note_rewritten":
                    assert isinstance(case.payload, str)
                    inputs = _sink_inputs(
                        case.call, None, None, ("output", case.payload), None, False, counts_file
                    )
                    output_sink.note_rewritten(case.payload)
                    yield _sink_line(
                        "generated",
                        inputs,
                        None,
                        None,
                        None if counts is None else str(counts),
                        counted,
                        None,
                    )
                    continue
                target = _sink_target(scratch / str(index), case)
                held_state, inode = _sink_held(target, blobs)
                raw = case.payload.encode("utf-8") if isinstance(case.payload, str) else case.payload
                inputs = _sink_inputs(
                    case.call,
                    target,
                    held_state,
                    (case.kind, _blob_into(blobs, raw)),
                    case.sha256,
                    case.keep,
                    counts_file,
                )
                try:
                    returned = _sink_make(case, target, scratch / f"{index}.source")
                except OSError, _Refused:
                    returned = ("raised",)
                yield _sink_line(
                    "generated",
                    inputs,
                    target,
                    inode,
                    None if counts is None else str(counts),
                    counted,
                    returned,
                )


CONTAINERS_STAGE = "containers"
# The `containers` stage's directory, beside its stage file, of the bytes its lines name by sha256: each container's input and each file a reader reads.
CONTAINER_BLOBS = "containers.blobs"
# The blob directory of the build whose `containers` stage records, for the length of that build (`_container_blobs_at`).
_CONTAINER_BLOBS_AT: list[Path] = []
# Held while a blob is written, since the index's members are deflated, and recorded, on the build's threads.
_CONTAINER_LOCK = threading.Lock()
_GZIP_INIT = inspect.signature(gzip.GzipFile.__init__)
_GZIP_COMPRESS = inspect.signature(gzip.compress)
_INFLATE_FRAGMENT = inspect.signature(unit_index.inflate_fragment)
# The length of the generated inputs' common source, past two of the index's members.
CONTAINER_BULK_BYTES = 2 * unit_index.INDEX_MEMBER_BYTES + 5
# The buffer `gzip.GzipFile` gathers its writes in before it deflates them, which typeshed does not declare.
GZIP_WRITE_BUFFER: int = gzip._WRITE_BUFFER_SIZE  # pyright: ignore[reportAttributeAccessIssue]
# The write sizes a generated stream's input is cut into, in turn: one byte, a few, a page less three, `gzip.GzipFile`'s write buffer (`GZIP_WRITE_BUFFER`), which a write of its size fills and a write one longer bypasses, nothing, an odd size and a little less than the buffer.
CONTAINER_CUTS = (
    1,
    7,
    4093,
    GZIP_WRITE_BUFFER,
    GZIP_WRITE_BUFFER + 1,
    0,
    65537,
    GZIP_WRITE_BUFFER - 5,
)
# The generated input lengths of the writers: nothing, a few bytes, less than `gzip.GzipFile`'s write buffer, past it, and past one of the index's members.
CONTAINER_LENGTHS = (0, 5, 200_000, GZIP_WRITE_BUFFER + 3, unit_index.INDEX_MEMBER_BYTES + 1)
# The names a generated `gzip.GzipFile` stream is opened under: each name the build's streams carry, and names with no name byte, with a Latin-1 one, with one outside Latin-1, with a directory and with only the suffix.
CONTAINER_NAMES = (
    unit_cache.STORE_NAME + output_sink.STAGING_SUFFIX,
    unit_cache.SIGNATURE_STORE_NAME,
    app_index.APP_INDEX_NAME + output_sink.STAGING_SUFFIX,
    app_index.LOCATOR_NAME + output_sink.STAGING_SUFFIX,
    f"{unit_cache.STORE_NAME}.3.2.delta",
    f"{unit_index.INDEX_NAME}.3.1.delta",
    "",
    "caf\xe9.tsv.gz",
    "Šumava.gz",
    "out/inner.tsv.gz",
    ".gz",
)
# The levels a generated container is written at: the two the build writes at (`app_index.COMPRESS_LEVEL` and every other writer's 1) and 9, whose header flag differs. Level 0 is left out: its stored blocks fall where the compressor's calls cut the input and the output, so not even two Python writers agree on it, and the build never writes it.
CONTAINER_LEVELS = (1, app_index.COMPRESS_LEVEL, 9)
# A mtime other than the build's 0, which a generated stream and member carry once.
CONTAINER_MTIME = 1_700_000_000


@dataclass
class _OpenContainer:
    """A container write the `containers` stage follows from its opening to its close: its settings, what was written to it, write by write, the file it writes, which holds only its bytes, and the handle to flush before that file is read."""

    settings: dict[str, object]
    writes: list[bytes]
    path: Path
    handle: IO[bytes]


# The `gzip.GzipFile` streams and `unit_index._Members` sinks open for writing, by `id`.
_OPEN_CONTAINERS: dict[int, _OpenContainer] = {}


@contextlib.contextmanager
def _container_blobs_at(directory: Path) -> Iterator[None]:
    _CONTAINER_BLOBS_AT.append(directory)
    try:
        yield
    finally:
        _CONTAINER_BLOBS_AT.pop()
        _OPEN_CONTAINERS.clear()


def _held(blobs: Path, data: bytes, within: bytes | None = None) -> tuple[str, int]:
    """A `containers` line's input: the sha256 of the blob that holds `data` and its length, the blob being `data` or, given `within`, the bytes `data` begins."""
    if within is not None:
        assert within.startswith(data)
    with _CONTAINER_LOCK:
        return _blob_into(blobs, data if within is None else within), len(data)


def _written(data: bytes) -> tuple[str, str, int]:
    return ("bytes", hashlib.sha256(data).hexdigest(), len(data))


def _gzip_read(data: bytes) -> tuple:
    """What `gzip.GzipFile` reads from `data` whole: `_written` of its bytes, or `("raised", class, message)`."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as stream:
            plain = stream.read()
    except (EOFError, gzip.BadGzipFile, zlib.error) as error:
        return ("raised", type(error).__name__, str(error))
    return _written(plain)


def _fragment_inflated(stored: bytes, length: int | None) -> tuple:
    try:
        return _written(unit_index.inflate_fragment(stored, length))
    except ValueError as error:
        return ("raised", "ValueError", str(error))


def _member_steps(data: bytes, wbits: int, piece: int, max_length: int) -> tuple:
    """The member readers' loop (`audit_stream._MemberReader`, `row_delta._Inflating`) over `data` read `piece` bytes at a time by one `zlib.decompressobj(wbits)`: each call inflates what the last left unconsumed, or the next read, at most `max_length` bytes, until the stream ends or nothing is left. Each call's output, unconsumed and unused lengths; then the sha256 and length of what the stream gave, whether it ended and what follows it, or the `zlib.error`'s text."""
    inflater = zlib.decompressobj(wbits)
    steps: list[tuple[int, int, int]] = []
    plain = bytearray()
    at = 0
    pending = b""
    while not inflater.eof:
        if not pending and at < len(data):
            pending = data[at : at + max(piece, 1)]
            at += len(pending)
        try:
            out = inflater.decompress(pending, max_length)
        except zlib.error as error:
            return ("raised", "error", str(error), tuple(steps))
        pending = inflater.unconsumed_tail
        plain += out
        steps.append((len(out), len(pending), len(inflater.unused_data)))
        if not out and not pending and at >= len(data):
            break
    return (
        "member",
        hashlib.sha256(plain).hexdigest(),
        len(plain),
        inflater.eof,
        len(inflater.unused_data),
        tuple(steps),
    )


def _container_line(
    origin: str, framing: str, inputs: Mapping[str, object], output: tuple
) -> dict[str, object]:
    """One `containers` line, keyed by its origin, its framing and a digest of its inputs, with its `output`."""
    fields = {"framing": framing, **inputs}
    digest = hashlib.sha1(compact(encode(fields)).encode("ascii")).hexdigest()[:20]
    return {"key": f"{origin}:{framing}:{digest}", **fields, "output": output}


def _read_line(origin: str, blobs: Path, data: bytes) -> dict[str, object]:
    """A `read` line: `data` read whole as a gzip file."""
    return _container_line(origin, "read", {"input": _held(blobs, data)}, _gzip_read(data))


def _written_file(handle: object) -> tuple[Path, IO[bytes]]:
    """The file a container is written into and the handle to flush before it is read: the handle itself, or the one a `row_delta._Counting` passes its bytes to."""
    inner = getattr(handle, "_handle", handle)
    name = getattr(inner, "name", None)
    if not isinstance(name, str):
        raise ValueError(f"the containers stage cannot find the file {handle!r} writes")
    return Path(name), inner  # pyright: ignore[reportReturnType]


def _closed_lines(
    origin: str, framing: str, opened: _OpenContainer, blobs: Path
) -> list[Mapping[str, object]]:
    """The lines of a container write that closed: the writer's, its settings, input and write sizes with the file's bytes, and a `read` line over that file."""
    if not opened.handle.closed:
        opened.handle.flush()
    data = opened.path.read_bytes()
    plain = b"".join(opened.writes)
    inputs = {
        **opened.settings,
        "input": _held(blobs, plain),
        "writes": tuple(len(piece) for piece in opened.writes),
    }
    return [_container_line(origin, framing, inputs, _written(data)), _read_line(origin, blobs, data)]


def _gzip_opened(args: tuple, kwargs: dict) -> Finisher:
    """Follow a `gzip.GzipFile` opened for writing into a file, from its name, level and mtime; one over memory (`build._SidecarLines`) writes no file the Rust build writes."""
    bound = _GZIP_INIT.bind(*args, **kwargs)
    bound.apply_defaults()
    arguments = bound.arguments

    def finish(_result: object) -> None:
        stream = arguments["self"]
        if stream.mode != gzip.WRITE or isinstance(stream.fileobj, io.BytesIO):
            return None
        if arguments["mtime"] is None or not isinstance(stream.name, str):
            raise ValueError("the containers stage records a gzip stream with a fixed mtime and a str name")
        if stream.myfileobj is not None:
            path, handle = Path(stream.name), stream.myfileobj
        else:
            path, handle = _written_file(stream.fileobj)
        settings: dict[str, object] = {
            "name": os.path.basename(stream.name),
            "level": arguments["compresslevel"],
            "mtime": arguments["mtime"],
        }
        _OPEN_CONTAINERS[id(stream)] = _OpenContainer(settings, [], path, handle)
        return None

    return finish


def _container_write(args: tuple, kwargs: dict) -> None:
    """Keep what a followed container is written, write by write."""
    opened = _OPEN_CONTAINERS.get(id(args[0]))
    if opened is not None:
        opened.writes.append(bytes(args[1] if len(args) > 1 else next(iter(kwargs.values()))))
    return None


def _gzip_closing(args: tuple, kwargs: dict) -> Finisher | None:
    opened = _OPEN_CONTAINERS.pop(id(args[0]), None)
    if opened is None or args[0].fileobj is None:
        return None
    blobs = _CONTAINER_BLOBS_AT[-1]
    return lambda _result: _closed_lines("run", "gzipfile", opened, blobs)


def _members_opened(args: tuple, kwargs: dict) -> Finisher:
    """Follow a `unit_index._Members` sink, from its member size and its threads, into the file it writes."""

    def finish(_result: object) -> None:
        sink = args[0]
        settings: dict[str, object] = {
            "member_bytes": sink._member_bytes,
            "member_threads": unit_index.INDEX_DEFLATE_THREADS,
        }
        _OPEN_CONTAINERS[id(sink)] = _OpenContainer(settings, [], *_written_file(sink._raw))
        return None

    return finish


def _members_exiting(args: tuple, kwargs: dict) -> Finisher | None:
    opened = _OPEN_CONTAINERS.pop(id(args[0]), None)
    if opened is None or (len(args) > 1 and args[1] is not None):
        return None
    blobs = _CONTAINER_BLOBS_AT[-1]
    return lambda _result: _closed_lines("run", "members", opened, blobs)


def _gzip_compressed(args: tuple, kwargs: dict) -> Finisher:
    """A `gzip.compress` member, a locator block's or an index member's, with a `read` line over it."""
    bound = _GZIP_COMPRESS.bind(*args, **kwargs)
    bound.apply_defaults()
    data, level, mtime = (
        bytes(bound.arguments["data"]),
        bound.arguments["compresslevel"],
        bound.arguments["mtime"],
    )
    if mtime is None:
        raise ValueError("the containers stage records a gzip member with a fixed mtime")
    blobs = _CONTAINER_BLOBS_AT[-1]

    def finish(result: object) -> list[Mapping[str, object]]:
        assert isinstance(result, bytes)
        inputs = {"level": level, "mtime": mtime, "input": _held(blobs, data)}
        return [
            _container_line("run", "compress", inputs, _written(result)),
            _read_line("run", blobs, result),
        ]

    return finish


def _fragment_deflated(args: tuple, kwargs: dict) -> Finisher:
    """A fragment's raw deflate, with an `inflate-fragment` line over it at its length."""
    body = bytes(args[0] if args else kwargs["body"])
    blobs = _CONTAINER_BLOBS_AT[-1]

    def finish(result: object) -> list[Mapping[str, object]]:
        assert isinstance(result, bytes)
        inflated = {"input": _held(blobs, result), "length": len(body)}
        return [
            _container_line("run", "raw", {"input": _held(blobs, body)}, _written(result)),
            _container_line("run", "inflate-fragment", inflated, _fragment_inflated(result, len(body))),
        ]

    return finish


def _fragment_inflating(args: tuple, kwargs: dict) -> Finisher:
    """A deflated fragment the build reads back, with the length it checks it against; a read that raises fails the build, so the stage records only the reads that return."""
    bound = _INFLATE_FRAGMENT.bind(*args, **kwargs)
    bound.apply_defaults()
    stored, length = bytes(bound.arguments["stored"]), bound.arguments["length"]
    blobs = _CONTAINER_BLOBS_AT[-1]

    def finish(result: object) -> dict[str, object]:
        assert isinstance(result, bytes)
        inputs = {"input": _held(blobs, stored), "length": length}
        return _container_line("run", "inflate-fragment", inputs, _written(result))

    return finish


def _container_bulk() -> bytes:
    """`CONTAINER_BULK_BYTES` of text-like lines with a run of random bytes inside, the same on every call, which the generated inputs take prefixes of."""
    rng = random.Random(CONTAINERS_STAGE)
    words = ["".join(rng.choices("abcdefghijklmnopqrstuvwxyz\xb7", k=rng.randint(1, 9))) for _ in range(512)]
    text = " ".join(rng.choices(words, k=CONTAINER_BULK_BYTES // 5))
    lines = "\n".join(text[start : start + 97] for start in range(0, len(text), 97)).encode("utf-8")
    bulk = lines[: 1 << 20] + rng.randbytes(100_000) + lines[1 << 20 :]
    assert len(bulk) >= CONTAINER_BULK_BYTES
    return bulk[:CONTAINER_BULK_BYTES]


def _uneven(data: bytes) -> list[bytes]:
    """`data` cut into `CONTAINER_CUTS`' sizes in turn."""
    pieces: list[bytes] = []
    at = 0
    for size in cycle(CONTAINER_CUTS):
        if at >= len(data):
            return pieces
        pieces.append(data[at : at + size])
        at += size
    return pieces


def _gzip_file_bytes(name: str, level: int, mtime: int, pieces: Sequence[bytes]) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(filename=name, fileobj=buffer, mode="wb", compresslevel=level, mtime=mtime) as stream:
        for piece in pieces:
            stream.write(piece)
    return buffer.getvalue()


def _members_bytes(pieces: Sequence[bytes]) -> bytes:
    buffer = io.BytesIO()
    with unit_index._Members(buffer) as sink:
        for piece in pieces:
            sink.write(piece)
    return buffer.getvalue()


def _crafted_member(flags: int, fields: bytes, plain: bytes) -> bytes:
    """A gzip member written by hand: `flags` and the optional fields `fields` after the fixed header, then `plain` deflated with its trailer."""
    header = b"\x1f\x8b\x08" + bytes([flags]) + b"\0\0\0\0\0\xff"
    trailer = zlib.crc32(plain).to_bytes(4, "little") + len(plain).to_bytes(4, "little")
    return header + fields + zlib.compress(plain, 1, wbits=-15) + trailer


def _flipped(data: bytes, at: int) -> bytes:
    changed = bytearray(data)
    changed[at] ^= 1
    return bytes(changed)


def _containers_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `containers` stage's generated lines: `gzip.GzipFile` streams under each name, at each level, over inputs to past one index member, cut into uneven writes, and at another mtime; `gzip.compress` members likewise; the index's members over inputs on both sides of each member boundary, whole and cut; fragments' raw deflate; `inflate_fragment` over a fragment at, short of and past its length, cut short, followed by bytes, and over bytes that do not inflate; whole-file reads of concatenated members, NUL padding, trailing bytes, every truncation, a bad CRC, a bad length, bad deflate data, another method and every header flag; the member readers' loop over whole, limited, trailed, cut and damaged members; and CRC-32s."""
    blobs = source.out / CONTAINER_BLOBS
    bulk = _container_bulk()

    def held(data: bytes) -> tuple[str, int]:
        return _held(blobs, data, bulk if bulk.startswith(data) else None)

    def gzip_file(name: str, level: int, mtime: int, data: bytes, pieces: Sequence[bytes], read: bool):
        output = _gzip_file_bytes(name, level, mtime, pieces)
        inputs = {
            "name": name,
            "level": level,
            "mtime": mtime,
            "input": held(data),
            "writes": tuple(len(piece) for piece in pieces),
        }
        yield _container_line("generated", "gzipfile", inputs, _written(output))
        if read:
            yield _read_line("generated", blobs, output)

    small = bulk[:300]
    for name in CONTAINER_NAMES:
        yield from gzip_file(name, 1, 0, small, [small], name == CONTAINER_NAMES[0])
    yield from gzip_file(unit_cache.SIGNATURE_STORE_NAME, 1, CONTAINER_MTIME, small, [small], True)
    for level in CONTAINER_LEVELS:
        for length in CONTAINER_LENGTHS:
            data = bulk[:length]
            read = length < unit_index.INDEX_MEMBER_BYTES or level == 1
            yield from gzip_file(unit_cache.SIGNATURE_STORE_NAME, level, 0, data, _uneven(data), read)
    for level in CONTAINER_LEVELS:
        for length in CONTAINER_LENGTHS:
            data = bulk[:length]
            output = gzip.compress(data, compresslevel=level, mtime=0)
            inputs = {"level": level, "mtime": 0, "input": held(data)}
            yield _container_line("generated", "compress", inputs, _written(output))
    inputs = {"level": 6, "mtime": CONTAINER_MTIME, "input": held(small)}
    yield _container_line(
        "generated",
        "compress",
        inputs,
        _written(gzip.compress(small, compresslevel=6, mtime=CONTAINER_MTIME)),
    )
    member = unit_index.INDEX_MEMBER_BYTES
    large_members = b""
    for length in (0, 5, member - 1, member, member + 1, 2 * member + 5):
        data = bulk[:length]
        for pieces in ([data] if data else [], _uneven(data)):
            output = _members_bytes(pieces)
            inputs = {
                "member_bytes": member,
                "member_threads": unit_index.INDEX_DEFLATE_THREADS,
                "input": held(data),
                "writes": tuple(len(piece) for piece in pieces),
            }
            yield _container_line("generated", "members", inputs, _written(output))
            yield _read_line("generated", blobs, output)
            if length == member + 1:
                large_members = output
    for length in CONTAINER_LENGTHS[:3]:
        data = bulk[:length]
        output = unit_index.deflate_fragment(data)
        yield _container_line("generated", "raw", {"input": held(data)}, _written(output))
    body = bulk[:2000]
    stored = unit_index.deflate_fragment(body)
    empty = unit_index.deflate_fragment(b"")
    for data, length in (
        (stored, None),
        (stored, 2000),
        (stored, 1999),
        (stored, 2001),
        (stored, 0),
        (stored[:-1], None),
        (stored[:-1], 2000),
        (stored[: len(stored) // 2], 2000),
        (stored + b"x", None),
        (stored + b"x", 2000),
        (b"\xff" * 20, None),
        (b"", None),
        (b"", 0),
        (empty, 0),
        (empty, None),
    ):
        inputs = {"input": _held(blobs, data), "length": length}
        yield _container_line("generated", "inflate-fragment", inputs, _fragment_inflated(data, length))
    one = _gzip_file_bytes("one.tsv.gz", 1, 0, [small])
    two = gzip.compress(bulk[300:700], compresslevel=6, mtime=0)
    plain = bulk[:400]
    deflated = zlib.compress(plain, 1, wbits=-15)
    reads = [
        one,
        two,
        one + two,
        one + b"\0\0\0",
        one + b"\0\0\0" + two,
        one + b"\0x",
        one + b"\x1f",
        one + b"\x1f\x8b",
        one + b"ab",
        one + b"'\"",
        one + b"x'",
        one + b"\\\t",
        b"",
        b"\0",
        b"\0\0",
        _flipped(one, len(one) - 8),
        _flipped(one, len(one) - 4),
        b"\x1f\x8b\x08\0\0\0\0\0\x04\xff" + b"\xff" * 20,
        b"\x1f\x8b\x07" + one[3:],
        _crafted_member(4, b"\x03\0abc", plain),
        _crafted_member(16, b"a note\0", plain),
        _crafted_member(2, b"\x12\x34", plain),
        _crafted_member(2 | 4 | 8 | 16, b"\x01\0zname\0note\0\x12\x34", plain),
        _crafted_member(0xE0, b"", plain),
        b"\x1f\x8b\x08\x08\0\0\0\0\0\xffname",
        b"\x1f\x8b\x08\x04\0\0\0\0\0\xff\x05\0ab",
        b"\x1f\x8b\x08\0\0\0\0\0\0\xff" + deflated[:-2],
        large_members,
    ]
    reads += [
        one[:cut] for cut in (1, 2, 5, 9, 10, 12, len(one) - 9, len(one) - 8, len(one) - 4, len(one) - 1)
    ]
    for data in reads:
        yield _read_line("generated", blobs, data)
    member_plain = bulk[:5000]
    single = gzip.compress(member_plain, compresslevel=1, mtime=0)
    raw = unit_index.deflate_fragment(member_plain)
    large_member = gzip.compress(bulk[:member], compresslevel=1, mtime=0)
    for data, wbits, piece, max_length in (
        (single, 31, len(single), 0),
        (single, 31, 7, 0),
        (single, 31, 7, 10),
        (single, 31, len(single), 100),
        (single + b"tail", 31, len(single) + 4, 0),
        (single + b"tail", 31, 7, 10),
        (single + b"tail", 31, len(single), 4999),
        (single[:-3], 31, 7, 0),
        (_flipped(single, len(single) - 8), 31, 64, 0),
        (_flipped(single, len(single) - 4), 31, 64, 0),
        (one, 31, 64, 0),
        (b"", 31, 1, 0),
        (b"\xff" * 10, 31, 4, 0),
        (raw + b"zz", -15, 9, 33),
        (large_member, 31, 1 << 16, 1 << 20),
    ):
        inputs = {"input": held(data), "wbits": wbits, "piece": piece, "max_length": max_length}
        yield _container_line(
            "generated", "member-read", inputs, _member_steps(data, wbits, piece, max_length)
        )
    for length in CONTAINER_LENGTHS:
        for start in (0, 0xDEADBEEF):
            data = bulk[:length]
            inputs = {"input": held(data), "start": start}
            yield _container_line("generated", "crc32", inputs, ("crc32", zlib.crc32(data, start)))


JSON_FORMS_STAGE = "json-forms"
# The modules whose `json.dumps` and `json.loads` calls the `json-forms` stage records: the parent's writers and readers of the files the Rust build writes and reads (the plan's § JSON forms). A call from anywhere else, the typed encoding's own among them, is not the parent's.
JSON_PARENT_MODULES = frozenset(
    {
        "rebuild.review.build",
        "rebuild.review.unit_cache",
        "rebuild.review.unit_index",
        "rebuild.review.app_index",
        "rebuild.review.facts",
        "rebuild.pipeline.row_delta",
    }
)
# The functions of those modules whose `json` calls write and read the key-only read's frames to and from its processes, which the Rust build replaces with threads, so no file holds them.
JSON_PROCESS_FRAMES = frozenset(
    {("rebuild.review.build", "_key_only_read"), ("rebuild.review.build", "_read_replies")}
)
_JSON_DUMPS = inspect.signature(json.dumps)
# The `json.dumps` forms the parent writes, by the name a `json-forms` line's `form` gives, each as the arguments past the value that differ from `json.dumps`' defaults; the Rust side writes each through its own entry point of `pyfmt::json`.
JSON_DUMPS_FORMS: dict[str, dict[str, Any]] = {
    "default": {},
    "indent": {"indent": 1},
    "sorted": {"sort_keys": True},
    "unicode": {"ensure_ascii": False},
    "compact": {"separators": (",", ":")},
}
JSON_LOADS = "loads"


def _json_parents_call() -> bool:
    """Whether the hooked `json` function was called by the parent (`JSON_PARENT_MODULES`) outside its process frames (`JSON_PROCESS_FRAMES`), told by the first frame outside this module, whose wrapper and recorders sit between the two."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_globals.get("__name__") == __name__:
        frame = frame.f_back
    if frame is None:
        return False
    module = frame.f_globals.get("__name__")
    return module in JSON_PARENT_MODULES and (module, frame.f_code.co_name) not in JSON_PROCESS_FRAMES


def _json_dumps_form(args: tuple, kwargs: dict) -> tuple[object, str]:
    """A `json.dumps` call's value and the form its arguments write (`JSON_DUMPS_FORMS`); arguments that match no form raise ValueError."""
    bound = _JSON_DUMPS.bind(*args, **kwargs)
    bound.apply_defaults()
    arguments = dict(bound.arguments)
    value = arguments.pop("obj")
    settings = {
        name: given
        for name, given in arguments.items()
        if name == "kw" and given or name != "kw" and given != _JSON_DUMPS.parameters[name].default
    }
    form = next((name for name, form in JSON_DUMPS_FORMS.items() if form == settings), None)
    if form is None:
        raise ValueError(f"the json-forms stage has no form for json.dumps with {settings!r}")
    return value, form


def _json_form_line(origin: str, form: str, value: object, output: tuple) -> dict[str, object]:
    """One `json-forms` line, keyed by its origin, its form and a digest of its input, with its `value`: `("text", text)` or `("value", value)` for what the call gave, or `("refused",)`."""
    digest = hashlib.sha1(compact(encode(value)).encode("ascii")).hexdigest()[:20]
    return {"key": f"{origin}:{form}:{digest}", "form": form, "input": value, "value": output}


def _json_dumped(args: tuple, kwargs: dict) -> Finisher | None:
    """A `json.dumps` call of the parent's, with the text it wrote."""
    if not _json_parents_call():
        return None
    value, form = _json_dumps_form(args, kwargs)
    return lambda result: _json_form_line("run", form, value, ("text", result))


def _json_loaded(args: tuple, kwargs: dict) -> Finisher | None:
    """A `json.loads` call of the parent's that returns, with what it read; one with arguments past the text raises ValueError."""
    if not _json_parents_call():
        return None
    if kwargs or len(args) != 1:
        raise ValueError(
            f"the json-forms stage records json.loads of one text, not {args!r:.80} {kwargs!r:.80}"
        )
    data = bytes(args[0]) if isinstance(args[0], bytearray) else args[0]
    return lambda result: _json_form_line("run", JSON_LOADS, data, ("value", result))


def _json_unheld(value: object) -> bool:
    """Whether `value`, as `json.loads` read it, holds what the Rust reader refuses: a float, `NaN` and `Infinity` among them, an int beyond 64 bits, or a str with a surrogate in it."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, float):
        return True
    if isinstance(value, int):
        return not -(2**63) <= value < 2**63
    if isinstance(value, str):
        return any(0xD800 <= ord(letter) <= 0xDFFF for letter in value)
    if isinstance(value, list):
        return any(_json_unheld(item) for item in value)
    if isinstance(value, dict):
        return any(_json_unheld(key) or _json_unheld(item) for key, item in value.items())
    raise TypeError(f"json.loads gave a {type(value).__name__}")


def _json_read(data: str | bytes) -> tuple:
    """What the Rust reader gives for `data`: `("value", what json.loads reads)`, or `("refused",)` where `json.loads` raises, where it decodes bytes as UTF-16 or UTF-32 (`json.detect_encoding`), and where what it reads holds what the Rust reader refuses (`_json_unheld`)."""
    if isinstance(data, bytes) and json.detect_encoding(data) not in ("utf-8", "utf-8-sig"):
        return ("refused",)
    try:
        value = json.loads(data)
    except ValueError:
        return ("refused",)
    return ("refused",) if _json_unheld(value) else ("value", value)


def _json_keyed_otherwise(value: object) -> bool:
    """Whether `value` holds a dict with a key that is not a str, which `json.dumps` stringifies or refuses and the Rust writers refuse."""
    if isinstance(value, (list, tuple)):
        return any(_json_keyed_otherwise(item) for item in value)
    if isinstance(value, dict):
        return any(not isinstance(key, str) or _json_keyed_otherwise(item) for key, item in value.items())
    return False


def _json_dumped_text(form: str, value: object) -> tuple:
    """What the Rust writer of `form` gives for `value`: `("text", what json.dumps writes)`, or `("refused",)` for a value with a key that is not a str (`_json_keyed_otherwise`) and for one `json.dumps` refuses."""
    if _json_keyed_otherwise(value):
        return ("refused",)
    try:
        return ("text", json.dumps(value, **JSON_DUMPS_FORMS[form]))
    except TypeError:
        return ("refused",)


def _json_dumps_edges() -> list[object]:
    """The values every writer form's generated lines put to it: every C0 control, DEL, Latin-1, non-ASCII within and beyond the BMP, the escapes, every scalar kind, the 64-bit edges, empty and nested containers, tuples, keys that sort differently by code point than by UTF-16, each kind of key `json.dumps` stringifies, and values it refuses."""
    controls = "".join(chr(point) for point in range(0x20))
    texts: list[str] = [
        "",
        "a",
        controls,
        "\x7f",
        "\"\\/'",
        "".join(chr(point) for point in range(0x20, 0x100)),
        "  ﻿",
        "·It ~b~ ·Day",
        "￿\U0001f600\U000e0001\U0010ffff",
        "é",
    ]
    scalars: list[object] = [0, 1, -1, 2**63 - 1, -(2**63), True, False, None]
    containers: list[object] = [
        (),
        [],
        {},
        (1,),
        ["a"],
        [()],
        [[], {}],
        ((((0, 1), (2, 3)), (), 0), ((), ((5, -1),), -2)),
        {"a": (1, -2)},
        {"k": [], "j": {}},
        {"b": 1, "a": 2, "B": 3, "\xe9": 4, "￿": 5, "\U0001f600": 6, "": 7, "": 8},
        {"z": {"y": [], "x": {}}, "a": [1, (2, 3), None, True, False, -7]},
        {"deep": {"a": [{"b": [{"c": []}]}]}, "n": -(2**63)},
        {text: [text, len(text)] for text in texts},
        {chr(point): point for point in range(0x20)},
        [texts, scalars],
    ]
    refused: list[object] = [
        {1: "a"},
        {True: 1},
        {None: [1]},
        {"a": {2: 3}},
        [{(1,): 2}],
        b"bytes",
        {"a": frozenset({1})},
    ]
    return texts + scalars + containers + refused


def _json_loads_edges() -> list[str | bytes]:
    """The texts the reader's generated lines put to it: the writers' edges as each form writes them, as a str and as UTF-8 bytes; JSON's whitespace and what is not; every escape, surrogate pairs and lone surrogates; raw control characters and raw non-ASCII; repeated keys; every number form, the 64-bit edges and past them; `NaN` and `Infinity`; malformed texts of each kind; byte order marks; text `json.detect_encoding` takes for UTF-16 or UTF-32; and bytes that are not UTF-8."""
    texts: list[str | bytes] = []
    for form, arguments in JSON_DUMPS_FORMS.items():
        for value in _json_dumps_edges():
            if _json_dumped_text(form, value)[0] == "text":
                written = json.dumps(value, **arguments)
                texts += [written, written.encode("utf-8")]
    texts += [
        ' {"a": 1} ',
        '\n\t\r {"a" :\n1 , "b":[ 1 ,2 ] }\r\n',
        '{"a":1,"b":2,"a":3}',
        '{"a":{"x":1},"a":[2]}',
        "[ ]",
        "{ }",
        '"x"',
        '{"":""}',
        '{"\\u0000":0}',
        "[[[[]]]]",
        '{"a":{"b":[{"c":[]}]}}',
        '"\\"\\\\\\/\\b\\f\\n\\r\\t"',
        '"\\u00e9\\u00E9\\uFFFF\\u0041"',
        '"\\ud83d\\ude00\\uD83D\\uDE00"',
        '"\\ud800"',
        '"\\udc00"',
        '"\\udbff\\udfff"',
        '"\\ud800\\u0041"',
        '"\\ud800x"',
        '"\\ud800\\uzzzz"',
        '"\\udc00\\ud800"',
        '"\\u12"',
        '"\\uzzzz"',
        '"\\x41"',
        '"\\a"',
        '"\\',
        '"\t"',
        '"\x00"',
        '"\x1f"',
        '"\x7f"',
        '"\xe9·\U0001f600 "',
        "0",
        "-0",
        "-1",
        "5",
        "9223372036854775807",
        "-9223372036854775808",
        "9223372036854775808",
        "-9223372036854775809",
        "123456789012345678901234567890",
        "1.5",
        "-0.0",
        "1e5",
        "1E+2",
        "1e-2",
        "[1.0]",
        "1.",
        "1e",
        "1e+",
        "01",
        "-",
        "+1",
        ".5",
        "--1",
        "-a",
        "NaN",
        "Infinity",
        "-Infinity",
        "[NaN]",
        '{"a":Infinity}',
        "nan",
        "Inf",
        "-Inf",
        "true",
        "false",
        "null",
        "tru",
        "nul",
        "nulll",
        "true false",
        "",
        " ",
        "﻿{}",
        "[1,]",
        '{"a":1,}',
        '{"a" 1}',
        "{a:1}",
        "{'a':1}",
        "[1 2]",
        '{"a":1 "b":2}',
        "{} x",
        "{}  \n",
        "{}\x0c",
        "\xa0{}",
        "[",
        "{",
        '{"a"',
        '{"a":',
        '"abc',
        "]",
        b'{"a": 1}\n',
        b'\xef\xbb\xbf{"a": 1}',
        b"\xef\xbb\xbf\xef\xbb\xbf{}",
        b"\xef\xbb\xbf",
        b"",
        b"5",
        b'"\xc3\xa9"',
        b'"\xff"',
        b'"\xed\xa0\x80"',
        b'"\xc0\xaf"',
        b'"\xf4\x90\x80\x80"',
        b"1\x00",
        b"\x001",
        b"\x00\x00\x001",
        b"1\x00\x00\x00",
        b"[1\x00 ]",
    ]
    for encoding in ("utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"):
        texts.append('{"a": [1, "\xe9"]}'.encode(encoding))
    return texts


def _json_forms_generated(_source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `json-forms` stage's generated lines: each writer form over `_json_dumps_edges`, and the reader over `_json_loads_edges`."""
    for form in JSON_DUMPS_FORMS:
        for value in _json_dumps_edges():
            yield _json_form_line("generated", form, value, _json_dumped_text(form, value))
    for data in _json_loads_edges():
        yield _json_form_line("generated", JSON_LOADS, data, _json_read(data))


SAMPLE_STAGE = "sample"
_VERIFICATION_SAMPLE = inspect.signature(build._verification_sample)
# The seeds the `sample` stage's generated lines draw from: empty, one letter, leading NUL bytes, which the seed's integer drops, non-ASCII, an environment stamp's hex digest, and keys of fewer and of more 32-bit words than the generator's 624 state words.
SAMPLE_SEEDS = (
    "",
    "a",
    "\x00",
    "\x00" * 9 + "seed",
    "é·Day \U0001f600",
    hashlib.sha256(b"environment").hexdigest(),
    "seed " * 400,
    "seed " * 600,
)
# The bounds the `randbelow` lines draw below: the smallest, each side of the 31-, 32-, 53- and 63-bit edges, and the largest a 64-bit id word allows.
SAMPLE_BOUNDS = (
    1,
    2,
    3,
    4,
    5,
    7,
    8,
    9,
    1000,
    *(edge + step for edge in (2**31, 2**32, 2**53, 2**63) for step in (-1, 0, 1)),
    2**64 - 1,
)


def _sample_line(origin: str, form: str, inputs: tuple, value: object) -> dict[str, object]:
    """One `sample` line, keyed by its origin, its form and a digest of its input, with what Python drew as its `value`."""
    digest = hashlib.sha1(compact(encode(inputs)).encode("ascii")).hexdigest()[:20]
    return {"key": f"{origin}:{form}:{digest}", "form": form, "input": inputs, "value": value}


def _verification_sampled(args: tuple, kwargs: dict) -> Finisher:
    """A `build._verification_sample` call, with the id words it draws."""
    bound = _VERIFICATION_SAMPLE.bind(*args, **kwargs)
    bound.apply_defaults()
    inputs = (list(bound.arguments["cached"]), bound.arguments["seed"], bound.arguments["size"])
    return lambda result: _sample_line("run", "verification", inputs, result)


def _setsize(k: int) -> int:
    """`random.Random.sample`'s `setsize` for `k` selections, as its source computes it, which tells the generated lines where the branch between the pool and the set turns."""
    return 21 + (4 ** math.ceil(math.log(k * 3, 4)) if k > 5 else 0)


def _sample_population(size: int) -> list[int]:
    """`size` distinct ints across the 64-bit range, ascending as the build's id words are."""
    return sorted((index * 0x9E3779B97F4A7C15 + 0x0123456789ABCDEF) % 2**64 for index in range(size))


def _sampled(seed: str, population: list[int], k: int) -> object:
    try:
        return random.Random(seed).sample(population, k)
    except ValueError:
        return ("refused",)


def _sample_generated(_source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `sample` stage's generated lines: `random.Random.sample` at every `k` over populations either side of `setsize`'s steps and around each step's edge, `getrandbits` at every width, `_randbelow` at `SAMPLE_BOUNDS` and `build._verification_sample` around its size, over `SAMPLE_SEEDS`."""
    stamp = SAMPLE_SEEDS[5]
    for seed, sized in ((stamp, _sample_population), ("a", lambda size: list(range(size)))):
        for size in (0, 1, 2, 5, 6, 7, 20, 21, 22, 23, 63, 64, 84, 85, 86, 100, 277, 278):
            population = sized(size)
            for k in range(-1, size + 2):
                yield _sample_line(
                    "generated", "sample", (seed, population, k), _sampled(seed, population, k)
                )
    for k in (5, 6, 21, 22, 85, 86, 341, 342, 1365, 1366):
        for size in (_setsize(k) - 1, _setsize(k), _setsize(k) + 1):
            population = _sample_population(size)
            yield _sample_line("generated", "sample", (stamp, population, k), _sampled(stamp, population, k))
    widths = (*range(65), *range(64, -1, -1))
    for seed in SAMPLE_SEEDS:
        drawing = random.Random(seed)
        yield _sample_line(
            "generated", "getrandbits", (seed, widths), [drawing.getrandbits(width) for width in widths]
        )
        drawing = random.Random(seed)
        yield _sample_line(
            "generated",
            "randbelow",
            (seed, SAMPLE_BOUNDS),
            [
                drawing._randbelow_with_getrandbits(bound)  # pyright: ignore[reportAttributeAccessIssue]
                for bound in SAMPLE_BOUNDS
            ],
        )
        for size in (0, 1, 2, 199, 200, 201, 1044, 1045, 1046, 3000):
            for limit in (0, 5, build.VERIFICATION_SAMPLE):
                inputs = (_sample_population(size), seed, limit)
                yield _sample_line("generated", "verification", inputs, build._verification_sample(*inputs))


ROW_DELTA_READ_STAGE = "row-delta-read"
# The `row-delta-read` stage's directory, beside its stage file, of the parts its lines name by sha256.
ROW_DELTA_BLOBS = "row-delta-read.blobs"
# The blob directory of the build whose `row-delta-read` stage records, for the length of that build (`_row_delta_blobs_at`).
_ROW_DELTA_BLOBS_AT: list[Path] = []
# Held while a blob is written, since a build may open parts on its threads.
_ROW_DELTA_LOCK = threading.Lock()
# The readers the `row-delta-read` recorders run themselves, taken before any build wraps them.
_READ_HEAD = row_delta.read_head
_OPEN_PARTS = row_delta.open_parts
_CHECK_SEGMENT_HEADER = row_delta.check_segment_header
_READ_HEAD_SIGNATURE = inspect.signature(row_delta.read_head)
_OPEN_PARTS_SIGNATURE = inspect.signature(row_delta.open_parts)
_CHECK_SEGMENT_HEADER_SIGNATURE = inspect.signature(row_delta.check_segment_header)
# A refusal whose text ends in what `json.loads` raised, which no output carries and the crate's reader does not word as Python's decoder does: the stage records it up to that colon.
_JSON_DETAIL = re.compile(r"(segment -?\d+ has no header|\S+ does not parse): .*", re.DOTALL)
# What a part's rows or a merge raises past the head: a `row_delta.HeadError` or another `ValueError`, Python's gzip reader's `EOFError` and `gzip.BadGzipFile`, and `zlib.error`.
_ROW_ERRORS = (ValueError, EOFError, OSError, zlib.error)
# The output the generated lines read under, and the generation their parts carry.
ROW_DELTA_OUTPUT = unit_cache.STORE_NAME
ROW_DELTA_GENERATION = 2
# A field a generated header leaves out.
_ABSENT = object()


@contextlib.contextmanager
def _row_delta_blobs_at(directory: Path) -> Iterator[None]:
    _ROW_DELTA_BLOBS_AT.append(directory)
    try:
        yield
    finally:
        _ROW_DELTA_BLOBS_AT.pop()


def _row_delta_line(origin: str, form: str, inputs: tuple, value: object) -> dict[str, object]:
    """One `row-delta-read` line, keyed by its origin, its form and a digest of its input, with what Python read as its `value`."""
    digest = hashlib.sha1(compact(encode(inputs)).encode("ascii")).hexdigest()[:20]
    return {"key": f"{origin}:{form}:{digest}", "form": form, "input": inputs, "value": value}


def _error_text(error: BaseException) -> str:
    message = str(error)
    cut = _JSON_DETAIL.fullmatch(message)
    return cut.group(1) if cut else message


def _row_delta_blob(blobs: Path, data: bytes) -> str:
    with _ROW_DELTA_LOCK:
        return _blob_into(blobs, data)


def _part_held(blobs: Path, path: Path) -> tuple:
    """What a part's path holds as an `open-parts` line names it: `("missing",)`, or `("file", sha256)` with its bytes kept in `blobs`."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ("missing",)
    return ("file", _row_delta_blob(blobs, data))


def _head_line(origin: str, directory: Path, output: str) -> dict[str, object]:
    """A `read-head` line: what the head's path holds, `("missing",)` or `("file", bytes)`, and what `row_delta.read_head` gives: `("none",)`, `("head", document)` or `("refused", message)`."""
    path = row_delta.head_path(directory, output)
    try:
        held: tuple = ("file", path.read_bytes())
    except FileNotFoundError:
        held = ("missing",)
    try:
        head = _READ_HEAD(directory, output)
    except row_delta.HeadError as error:
        value: tuple = ("refused", _error_text(error))
    else:
        value = ("none",) if head is None else ("head", head.document())
    return _row_delta_line(origin, "read-head", (output, held), value)


def _header_line(origin: str, inputs: tuple) -> dict[str, object]:
    """A `check-segment-header` line: the header line, output, generation, number and merge, and `("checked",)` or `("refused", message)`."""
    try:
        _CHECK_SEGMENT_HEADER(*inputs)
    except row_delta.HeadError as error:
        return _row_delta_line(origin, "check-segment-header", inputs, ("refused", _error_text(error)))
    return _row_delta_line(origin, "check-segment-header", inputs, ("checked",))


def _rows_outcome(rows: Iterable[object]) -> tuple:
    """What reading `rows` through gives: `("rows", count, sha256)` over each item's typed encoding and a newline, as `column_line` digests a column, or `("raised", class, message)` for the first error."""
    digest = hashlib.sha256()
    count = 0
    try:
        for item in rows:
            digest.update(compact(encode(item)).encode("ascii") + b"\n")
            count += 1
    except _ROW_ERRORS as error:
        return ("raised", type(error).__name__, _error_text(error))
    return ("rows", count, digest.hexdigest())


def _part_items(kind: str, handle: BinaryIO, output: str, generation: int, number: int) -> Iterator[object]:
    """One part's rows as its reader reads them: a base's lines past its header line (`unit_index._base_lines`, the index's reader), a segment by place's `(op, anchor, fraction, payload)` (`row_delta.segment_rows`), or a segment by key's `(op, payload)` (`row_delta.keyed_segment_rows`)."""
    if kind == "base":
        yield from unit_index._base_lines(handle)
    elif kind == row_delta.MERGE_PLACE:
        for row in row_delta.segment_rows(handle, output, generation, number):
            yield (row.op, row.place[0], row.place[1], row.payload)
    else:
        yield from row_delta.keyed_segment_rows(handle, output, generation, number)


def _merged_items(
    merge: str,
    handles: Sequence[BinaryIO],
    output: str,
    generation: int,
    key: Callable[[bytes], list[bytes]] | None,
) -> Iterator[object]:
    """The materialized rows of a base and its segments: `(place, payload)` from `row_delta.merge_placed`, as `unit_index.store_lines` merges the store's segments, or each payload from `row_delta.merge_keyed` under `key`, as `unit_index.OpenIndex.lines` merges the index; the base is read as `unit_index._base_lines` reads the index's either way."""
    base = handles[0]
    if key is None:
        segments = [
            (lambda handle=handle, number=number: row_delta.segment_rows(handle, output, generation, number))
            for number, handle in enumerate(handles[1:], start=1)
        ]
        yield from row_delta.merge_placed(lambda: unit_index._base_lines(base), segments)
        return
    keyed = [
        row_delta.keyed_segment_rows(handle, output, generation, number)
        for number, handle in enumerate(handles[1:], start=1)
    ]
    yield from row_delta.merge_keyed(unit_index._base_lines(base), keyed, key, key)


def _merge_outcome(
    merge: str,
    handles: Sequence[BinaryIO],
    output: str,
    generation: int,
    key: Callable[[bytes], list[bytes]] | None,
) -> tuple[tuple, tuple]:
    """A merge's outcome (`_merged_items`, `_rows_outcome`) and, by key, each distinct payload the merge took a key of with that key, in the order it took them, which the Rust side's merge looks up in place of the caller's key function."""
    if key is None:
        return _rows_outcome(_merged_items(merge, handles, output, generation, None)), ()
    taken: dict[bytes, list[bytes]] = {}

    def keyed(payload: bytes) -> list[bytes]:
        found = key(payload)
        taken.setdefault(payload, found)
        return found

    outcome = _rows_outcome(_merged_items(merge, handles, output, generation, keyed))
    return outcome, tuple(taken.items())


def _read_lines(
    origin: str,
    head: row_delta.Head,
    handles: Sequence[BinaryIO],
    digests: Sequence[str],
    key: Callable[[bytes], list[bytes]] | None,
) -> list[Mapping[str, object]]:
    """A `part-rows` line a part of an opened head, its kind, output, generation, number and file, and a `merge` line over all of them, its merge, output, generation, files and, by key, the payloads' keys."""
    lines: list[Mapping[str, object]] = []
    for number, (handle, digest) in enumerate(zip(handles, digests)):
        kind = "base" if number == 0 else head.merge
        inputs = (kind, head.output, head.generation, number, digest)
        outcome = _rows_outcome(_part_items(kind, handle, head.output, head.generation, number))
        lines.append(_row_delta_line(origin, "part-rows", inputs, outcome))
    outcome, keys = _merge_outcome(head.merge, handles, head.output, head.generation, key)
    inputs = (head.merge, head.output, head.generation, tuple(digests), keys)
    lines.append(_row_delta_line(origin, "merge", inputs, outcome))
    return lines


def _opened_lines(
    origin: str, blobs: Path, directory: Path, head: row_delta.Head, sha256: bool
) -> list[Mapping[str, object]]:
    """An `open-parts` line, the head's document, `sha256` and what each part's path holds, with `("opened",)` or `("refused", message)`; and, for a head that opens and merges by place, or is the units index's, its parts' rows and its merge (`_read_lines`), read through parts this opens itself."""
    files = tuple((part.name, _part_held(blobs, directory / part.name)) for part in head.parts)
    inputs = (head.document(), sha256, files)
    try:
        opened = _OPEN_PARTS(directory, head, sha256=sha256)
    except row_delta.HeadError as error:
        return [_row_delta_line(origin, "open-parts", inputs, ("refused", _error_text(error)))]
    with opened:
        lines: list[Mapping[str, object]] = [_row_delta_line(origin, "open-parts", inputs, ("opened",))]
        if head.merge == row_delta.MERGE_PLACE:
            key = None
        elif head.merge == row_delta.MERGE_KEY and head.output == unit_index.INDEX_NAME:
            key = unit_index.index_key
        else:
            return lines
        digests = [held[1] for _name, held in files]
        lines += _read_lines(origin, head, opened.handles, digests, key)
    return lines


def _head_read(args: tuple, kwargs: dict) -> Finisher:
    """A `row_delta.read_head` call, read again before it runs, so a refusal is recorded too."""
    bound = _READ_HEAD_SIGNATURE.bind(*args, **kwargs)
    line = _head_line("run", Path(bound.arguments["directory"]), bound.arguments["output"])
    return lambda _result: line


def _parts_opened(args: tuple, kwargs: dict) -> Finisher:
    """A `row_delta.open_parts` call, opened and read again before it runs (`_opened_lines`)."""
    bound = _OPEN_PARTS_SIGNATURE.bind(*args, **kwargs)
    bound.apply_defaults()
    arguments = bound.arguments
    lines = _opened_lines(
        "run", _ROW_DELTA_BLOBS_AT[-1], Path(arguments["directory"]), arguments["head"], arguments["sha256"]
    )
    return lambda _result: lines


def _segment_header_checked(args: tuple, kwargs: dict) -> Finisher:
    """A `row_delta.check_segment_header` call, checked again before it runs."""
    bound = _CHECK_SEGMENT_HEADER_SIGNATURE.bind(*args, **kwargs)
    arguments = bound.arguments
    inputs = (
        bytes(arguments["line"]),
        arguments["output"],
        arguments["generation"],
        arguments["number"],
        arguments["merge"],
    )
    line = _header_line("run", inputs)
    return lambda _result: line


def _case_directory(scratch: Path, files: Mapping[str, bytes]) -> Path:
    """A fresh directory under `scratch` holding `files` by name."""
    directory = Path(tempfile.mkdtemp(dir=scratch))
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return directory


def _gz(*lines: bytes) -> bytes:
    return gzip.compress(b"".join(lines), compresslevel=1, mtime=0)


def _head_bytes(document: object) -> bytes:
    return json.dumps(document).encode()


def _part_document(name: str, /, **changes: object) -> dict[str, object]:
    document: dict[str, object] = {"name": name, "size": 10, "sha256": "0" * 64, "rows": 2, "rows_bytes": 30}
    document.update(changes)
    return document


def _head_document(generation: object = ROW_DELTA_GENERATION, **changes: object) -> dict[str, object]:
    """A head of `ROW_DELTA_OUTPUT` at `ROW_DELTA_GENERATION` with a base and two segments, `changes` set over it."""
    output = ROW_DELTA_OUTPUT
    named = ROW_DELTA_GENERATION if not isinstance(generation, int) else generation
    document: dict[str, object] = {
        "format": row_delta.HEAD_FORMAT,
        "output": output,
        "generation": generation,
        "content": 7,
        "stamps": {"format": "stamp", "parts": {"a.json": 3}, "lines": ["x\ty"]},
        "base": _part_document(row_delta.base_name(output, named)),
        "segments": [
            _part_document(row_delta.segment_name(output, named, 1)),
            _part_document(row_delta.segment_name(output, named, 2)),
        ],
    }
    document.update(changes)
    return document


def _without(document: Mapping[str, object], *names: str) -> dict[str, object]:
    return {name: value for name, value in document.items() if name not in names}


def _base_part(**changes: object) -> dict[str, object]:
    return _part_document(row_delta.base_name(ROW_DELTA_OUTPUT, ROW_DELTA_GENERATION), **changes)


def _with_base(**changes: object) -> dict[str, object]:
    return _head_document(base=_base_part(**changes))


def _head_cases() -> Iterator[bytes]:
    """The heads the generated `read-head` lines read: each refusal of `row_delta.parse_head` and `Part.parse`, each lenient read of a value `int` and `str` take, and raw bytes that do not parse."""
    base = _head_document()
    member_base = _with_base(size=9, rows_bytes=5, members=[["", 4, 3], ["default", 5, 5]])
    documents: list[object] = [
        base,
        _head_document(merge=row_delta.MERGE_PLACE),
        *(_head_document(merge=merge) for merge in row_delta._MERGES),
        _head_document(merge="bogus"),
        _head_document(merge=5),
        _head_document(merge=None),
        _head_document(format="ams-row-delta-head/2"),
        _without(base, "format"),
        _head_document(output="units-index.ndjson.gz"),
        _head_document(output="Šumava's"),
        _head_document(output=5),
        _without(base, "output"),
        _without(base, "stamps"),
        _head_document(stamps=[]),
        _without(base, "segments"),
        _head_document(segments={}),
        _head_document(segments=[]),
        _without(base, "generation"),
        _without(base, "content"),
        _head_document(generation="2"),
        _head_document(generation=" 2 "),
        _head_document(generation="+2"),
        _head_document(generation="0_2"),
        _head_document(generation="x"),
        _head_document(generation="2.0"),
        _head_document(generation=""),
        _head_document(generation=None),
        _head_document(generation=[2]),
        _head_document(generation=True),
        _head_document(generation=1, content=True),
        _head_document(content="7"),
        _head_document(content={"a": 1}),
        _head_document(family_keys=[]),
        _head_document(family_keys={}),
        _head_document(family_keys=[{"qsPea": "k1"}, {}]),
        _head_document(family_keys=[{"qsPea": 1}]),
        _head_document(family_keys=[[]]),
        _head_document(family_keys=["x"]),
        _without(base, "base"),
        _head_document(base=[]),
        _head_document(base="x"),
        _head_document(segments=[5]),
        _head_document(segments=[None]),
        _head_document(base=_without(_base_part(), "name")),
        _head_document(base=_without(_base_part(), "size")),
        _head_document(base=_without(_base_part(), "rows_bytes")),
        _with_base(size="12"),
        _with_base(size=" 12\n"),
        _with_base(size="1_2"),
        _with_base(size="1__2"),
        _with_base(size="_12"),
        _with_base(size="-12"),
        _with_base(size="١٢"),
        _with_base(size="x" * 300),
        _with_base(size="1" * 4301),
        _with_base(size="1" * 4301 + "\x00"),
        _with_base(size="it's"),
        _with_base(size=None),
        _with_base(size=[]),
        _with_base(size={}),
        _with_base(size=True),
        _with_base(size=-5),
        _with_base(rows="3"),
        _with_base(rows_bytes=False),
        _with_base(name=5),
        _with_base(name=None),
        _with_base(name=["a"]),
        _with_base(sha256=5),
        _with_base(sha256={"a": [1, None, True]}),
        _head_document(base=_base_part(stamp="s", family_keys=0), family_keys=[{"qsPea": "k1"}]),
        _with_base(stamp="s"),
        _with_base(family_keys=0),
        _with_base(stamp=5, family_keys=0),
        _with_base(stamp="s", family_keys=True),
        _with_base(stamp="s", family_keys="0"),
        _head_document(base=_base_part(stamp="s", family_keys=3), family_keys=[{"qsPea": "k1"}]),
        _head_document(base=_base_part(stamp="s", family_keys=-1), family_keys=[{"qsPea": "k1"}]),
        member_base,
        _with_base(members="x"),
        _with_base(members=[]),
        _with_base(members=[["", 1]]),
        _with_base(members=[["", 1, True]]),
        _with_base(members=[["", -1, 2]]),
        _with_base(members=[[1, 2, 3]]),
        _with_base(members=["a"]),
        _with_base(size=5, rows_bytes=0, members=[["a", 5, 5]]),
        _with_base(size=9, rows_bytes=5, members=[["", 4, 3], ["default", 4, 5]]),
        _with_base(size=9, rows_bytes=5, members=[["", 4, 3], ["default", 5, 6]]),
        _with_base(size=9, rows_bytes=5, members=[["", 4, 3], ['it\'s "x"', 5, 5]]),
        _head_document(
            segments=[
                _part_document(row_delta.segment_name(ROW_DELTA_OUTPUT, ROW_DELTA_GENERATION, 2)),
            ]
        ),
        _head_document(base=_part_document(row_delta.base_name(ROW_DELTA_OUTPUT, ROW_DELTA_GENERATION + 1))),
        _head_document(
            base=_part_document(row_delta.base_name(ROW_DELTA_OUTPUT, 1)),
            segments=[_part_document(row_delta.segment_name(ROW_DELTA_OUTPUT, 1, 1))],
        ),
        _head_document(
            generation=True, base=_part_document(row_delta.base_name(ROW_DELTA_OUTPUT, 1)), segments=[]
        ),
        [],
        None,
        "head",
        5,
    ]
    for document in documents:
        yield _head_bytes(document)
    valid = _head_bytes(base)
    yield from (
        b"",
        b"   \n",
        b"{",
        b"{} x",
        b'{"a": 1,}',
        b"\xff\xfe",
        b'{"format": "\xff"}',
        b"\xef\xbb\xbf" + valid,
        b'{"generation": 1, "generation": 2}',
        valid.replace(b'"content": 7', b'"content": 7, "content": 9'),
        valid.replace(b'"content": 7', b'"content": ' + b"7" * 4301),
        valid.replace(b'"format": "ams', b'"format": "\\u0061ms'),
    )


def _segment_header_cases() -> Iterator[tuple]:
    """The generated `check-segment-header` inputs: each refusal of `row_delta.check_segment_header`, headers it reads past, and `==`'s reading of a bool as an int."""
    output, generation = ROW_DELTA_OUTPUT, ROW_DELTA_GENERATION
    place = row_delta.segment_header(output, generation, 1)
    keyed = row_delta.segment_header(output, generation, 1, row_delta.MERGE_KEY)

    def header(**changes: object) -> bytes:
        document: dict[str, object] = {
            "format": row_delta.SEGMENT_FORMAT,
            "output": output,
            "generation": generation,
            "segment": 1,
        }
        document.update(changes)
        return (
            json.dumps({name: value for name, value in document.items() if value is not _ABSENT}) + "\n"
        ).encode()

    for line, merge in (
        (place, row_delta.MERGE_PLACE),
        (keyed, row_delta.MERGE_KEY),
        (keyed, row_delta.MERGE_PLACE),
        (place, row_delta.MERGE_KEY),
        (place, row_delta.MERGE_PLAIN_KEY),
        (header(merge=row_delta.MERGE_PLACE), row_delta.MERGE_PLACE),
        (header(merge=None), row_delta.MERGE_PLACE),
        (header(merge=5), row_delta.MERGE_KEY),
        (header(merge={"a": [1]}), row_delta.MERGE_KEY),
        (header(merge="it's"), row_delta.MERGE_KEY),
        (header(generation=generation + 1), row_delta.MERGE_PLACE),
        (header(segment=2), row_delta.MERGE_PLACE),
        (header(segment=_ABSENT), row_delta.MERGE_PLACE),
        (header(segment="1"), row_delta.MERGE_PLACE),
        (header(segment=True), row_delta.MERGE_PLACE),
        (header(segment=[1], generation=None), row_delta.MERGE_PLACE),
        (header(output="units-index.ndjson.gz"), row_delta.MERGE_PLACE),
        (header(output=_ABSENT), row_delta.MERGE_PLACE),
        (header(format="ams-row-delta-segment/2"), row_delta.MERGE_PLACE),
        (header(format=_ABSENT), row_delta.MERGE_PLACE),
        (header(extra={"x": 1}), row_delta.MERGE_PLACE),
        (header().replace(b'"segment": 1', b'"segment": ' + b"1" * 4301), row_delta.MERGE_PLACE),
        (b"[]\n", row_delta.MERGE_PLACE),
        (b"5", row_delta.MERGE_PLACE),
        (b"", row_delta.MERGE_PLACE),
        (b"\n", row_delta.MERGE_PLACE),
        (b"{", row_delta.MERGE_PLACE),
        (b"\xff\n", row_delta.MERGE_PLACE),
        (place.rstrip(b"\n"), row_delta.MERGE_PLACE),
    ):
        yield (line, output, generation, 1, merge)
    yield (header(generation=True, segment=True), output, 1, 1, row_delta.MERGE_PLACE)
    yield (header(output="Šumava"), "Šumava", generation, 1, row_delta.MERGE_PLACE)


def _place_segment(number: int, rows: Sequence[bytes], generation: int = ROW_DELTA_GENERATION) -> bytes:
    return _gz(row_delta.segment_header(ROW_DELTA_OUTPUT, generation, number), *rows)


def _keyed_segment(number: int, rows: Sequence[bytes]) -> bytes:
    header = row_delta.segment_header(ROW_DELTA_OUTPUT, ROW_DELTA_GENERATION, number, row_delta.MERGE_KEY)
    return _gz(header, *rows)


def _part_rows_cases() -> Iterator[tuple[str, int, bytes]]:
    """The generated `part-rows` parts, each with its kind and number: bases read whole, short, empty, cut and damaged; segments by place with every row `_parse_row` refuses and every anchor `int` reads; segments by key with every row `_keyed_rows` refuses; and segments whose headers, gzip framing or trailers refuse."""
    rows = [b'{"key": "%d"}\n' % index for index in range(6)]
    base = _gz(b'{"format": "x"}\n', *rows)
    yield from (
        ("base", 0, base),
        ("base", 0, _gz(b"header\n")),
        ("base", 0, _gz(b"header")),
        ("base", 0, b""),
        ("base", 0, _gz()),
        ("base", 0, _gz(b"\n", *rows)),
        ("base", 0, _gz(b"h\n", b"a\n", b"b")),
        ("base", 0, base + _gz(b"more\n") + b"\0\0"),
        ("base", 0, base[:-6]),
        ("base", 0, base[:15]),
        ("base", 0, _flipped(base, len(base) - 8)),
        ("base", 0, base + b"xy"),
        ("base", 0, b"plain\nlines\n"),
    )
    good = [
        b'+\t-1\ta\t{"key": "n"}\n',
        b'-\t0\t\t{"key": "0"}\n',
        b'+\t1\t\t{"key": "1x"}\n',
        b"+\t3\t0i\tpayload\twith\ttabs\n",
        b"+\t5\tz\tlast",
    ]
    yield ("place", 1, _place_segment(1, good))
    yield ("place", 2, _place_segment(2, good[:2]))
    yield ("place", 1, _place_segment(1, []))
    for anchor in (
        b" 7 ",
        b"+2",
        b"-0",
        b"1_0",
        b"007",
        b"\x0b3\x0c",
        b"\r4",
        b"9223372036854775807",
        b"-9223372036854775808",
    ):
        yield ("place", 1, _place_segment(1, [b"+\t" + anchor + b"\tf\tp\n"]))
    for bad in (
        b"+\tx\t\tp\n",
        b"+\t\t\tp\n",
        b"+\t1__0\t\tp\n",
        b"+\t_1\t\tp\n",
        b"+\t1 2\t\tp\n",
        b"+\t" + b"x" * 250 + b"\t\tp\n",
        b"+\t" + b"x" * 250 + b"'\t\tp\n",
        b"+\t" + b"1" * 4301 + b"\t\tp\n",
        b"+\t" + b"1" * 4301 + b"\x00\t\tp\n",
        b"+\t1\t\xc3\xa9\tp\n",
        b"+\t1\ta\xff\tp\n",
        b"*\t1\t\tp\n",
        b"++\t1\t\tp\n",
        b"\t1\t\tp\n",
        b"+\t1\n",
        b"+\t1\t",
        b"+\n",
        b"+",
    ):
        yield ("place", 1, _place_segment(1, [good[0], bad, good[1]]))
    segment = _place_segment(1, good)
    yield from (
        ("place", 2, segment),
        ("place", 1, _place_segment(1, good, ROW_DELTA_GENERATION + 1)),
        ("place", 1, _keyed_segment(1, [])),
        ("place", 1, _gz(b"not json\n", *good)),
        ("place", 1, _gz()),
        ("place", 1, b""),
        ("place", 1, b"plain segment\n"),
        ("place", 1, b"\x1f\x8b\x07" + segment[3:]),
        ("place", 1, segment[:-5]),
        ("place", 1, segment[:12]),
        ("place", 1, _flipped(segment, len(segment) - 7)),
        ("place", 1, segment + b"junk"),
        ("place", 1, segment + b"\0\0" + _gz(b"+\t9\t\tq\n")),
    )
    keyed = [b"+\tk1\tv\n", b"-\tk2\tx\n", b"+\tk3", b"-\t\n", b"+\t\t\n"]
    yield ("key", 1, _keyed_segment(1, keyed))
    yield ("key", 3, _keyed_segment(3, keyed[:1]))
    for bad in (b"+k1\n", b"x\tk1\n", b"+", b"\n", b"++\tk\n", b"-"):
        yield ("key", 1, _keyed_segment(1, [keyed[0], bad]))
    yield ("key", 1, _place_segment(1, []))
    yield ("key", 2, _keyed_segment(1, keyed))
    yield ("key", 1, b"\x00\x01")


def _tab_key(payload: bytes) -> list[bytes]:
    """The generated merges' key: a row's first two tab-separated fields."""
    return payload.rstrip(b"\n").split(b"\t")[:2]


def _merge_cases() -> Iterator[tuple[str, list[bytes]]]:
    """The generated merges, each its merge and its parts, base first: by place, upserts, tombstones and inserts superseded by a later segment, before the first row and past the last, with no segment, and over a segment that stops reading; by key, the same over keys of one and two fields, a segment or a base out of key order, a base out of order past the last change, and a segment or a base that stops reading."""
    rows = [b"row %d\n" % index for index in range(6)]
    base = _gz(b'{"format": "x"}\n', *rows)
    first = _place_segment(
        1,
        [
            b"+\t-1\tk\tbefore\n",
            b"-\t0\t\tgone\n",
            b"+\t1\t\tchanged\n",
            b"-\t2\t\tgone\n",
            b"+\t2\t8\tafter two\n",
            b"+\t5\t\tlast changed\n",
            b"+\t9\t\tpast the end\n",
        ],
    )
    second = _place_segment(
        2,
        [
            b"-\t-1\tk\tgone again\n",
            b"+\t1\t\tchanged twice\n",
            b"+\t2\t\trevived\n",
            b"-\t2\t8\tgone\n",
            b"+\t3\t4\tbetween\n",
            b"+\t9\t\tpast the end, later\n",
        ],
    )
    yield (row_delta.MERGE_PLACE, [base, first, second])
    yield (row_delta.MERGE_PLACE, [base])
    yield (row_delta.MERGE_PLACE, [base, first])
    yield (row_delta.MERGE_PLACE, [_gz(b"h\n"), first])
    yield (row_delta.MERGE_PLACE, [base, first, _place_segment(2, [b"+\tx\t\tbad\n"])])
    yield (row_delta.MERGE_PLACE, [base, _place_segment(2, [])])
    yield (row_delta.MERGE_PLACE, [base[:-6], first])
    yield (row_delta.MERGE_PLACE, [base, first[:-6]])
    yield (row_delta.MERGE_PLACE, [base, first, _place_segment(2, [b"+\t0\t\tz\n", b"+\t0\t\ty\n"])])
    keyed_rows = [b"a\t1\tone\n", b"b\t1\ttwo\n", b"b\t2\tthree\n", b"d\t\tfour\n", b"f\t1\tfive\n"]
    keyed_base = _gz(b'{"format": "x"}\n', *keyed_rows)
    keyed_first = _keyed_segment(
        1,
        [
            b"+\t0\t\tbefore all\n",
            b"-\ta\t1\n",
            b"+\tb\t1\tchanged\n",
            b"+\tc\tnew\n",
            b"-\tf\t1\n",
            b"+\tz\tpast all\n",
        ],
    )
    keyed_second = _keyed_segment(
        2,
        [
            b"+\ta\t1\trevived\n",
            b"-\tb\t1\n",
            b"+\tb\t2\tchanged later\n",
            b"-\tc\tnew\n",
            b"+\tz\tpast all, later\n",
        ],
    )
    yield (row_delta.MERGE_KEY, [keyed_base, keyed_first, keyed_second])
    yield (row_delta.MERGE_KEY, [keyed_base])
    yield (row_delta.MERGE_KEY, [keyed_base, keyed_first])
    yield (row_delta.MERGE_KEY, [keyed_base, _keyed_segment(1, [b"+\tb\t1\tx\n", b"+\ta\t1\ty\n"])])
    yield (row_delta.MERGE_KEY, [keyed_base, _keyed_segment(1, [b"+\tc\tx\n", b"-\tc\n"])])
    out_of_order = _gz(b"h\n", b"b\tx\n", b"a\tx\n", b"c\tx\n", b"e\tx\n", b"d\tx\n")
    yield (row_delta.MERGE_KEY, [out_of_order, _keyed_segment(1, [b"+\tc\ty\n"])])
    yield (row_delta.MERGE_KEY, [out_of_order, _keyed_segment(1, [b"+\ta\ty\n"])])
    yield (
        row_delta.MERGE_KEY,
        [_gz(b"h\n", b"a\tx\n", b"c\tx\n", b"b\tx\n"), _keyed_segment(1, [b"+\ta\ty\n"])],
    )
    yield (row_delta.MERGE_KEY, [keyed_base, keyed_first, _keyed_segment(2, [b"x\tbad\n"])])
    yield (row_delta.MERGE_KEY, [keyed_base, _keyed_segment(1, [])])
    yield (row_delta.MERGE_KEY, [keyed_base[:-6], keyed_first])
    yield (row_delta.MERGE_KEY, [keyed_base, keyed_first[:-6], keyed_second])
    yield (row_delta.MERGE_KEY, [keyed_base, _place_segment(1, [])])


def _member(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=1, mtime=0)


def _member_part(
    sections: Sequence[tuple[str, bytes]], recorded: Sequence[tuple[int, int]] | None = None
) -> tuple[dict, bytes]:
    """A part held as gzip members, a section a member, and its document, whose index records each member's length and inflated length, or `recorded` in their place; its size and row bytes are the recorded sums, so `Part.parse` takes it."""
    members = [_member(data) for _section, data in sections]
    index = (
        recorded
        if recorded is not None
        else [(len(member), len(data)) for member, (_s, data) in zip(members, sections)]
    )
    document = {
        "name": f"audit.tsv.{ROW_DELTA_GENERATION}.base",
        "size": sum(length for length, _inflated in index),
        "sha256": "0" * 64,
        "rows": len(sections) - 1,
        "rows_bytes": sum(inflated for _length, inflated in index[1:]),
        "members": [
            [section, length, inflated] for (section, _data), (length, inflated) in zip(sections, index)
        ],
    }
    return document, b"".join(members)


def _inflating_cases() -> Iterator[tuple[dict, bytes]]:
    """The generated `inflating` parts: members read whole, over more than one read, and each refusal of `row_delta._Inflating`: a file that ends inside a member, a member that runs past or ends before its recorded length, inflates past or short of its recorded length, or does not inflate."""
    bulk = _container_bulk()
    header = b"config\twindow\n"
    sections = [("", header), ("default", bulk[:5000]), ("ss03", bulk[5000:300_000]), ("ss05", b"")]
    document, data = _member_part(sections)
    yield document, data
    lengths = [entry[1:] for entry in document["members"]]
    yield document, data[:-10]
    yield document, data[: lengths[0][0] + 7]
    yield _member_part(sections[:1])
    yield _member_part([("", b""), ('it\'s "q"', b"x\n")])
    shifted = [lengths[0], (lengths[1][0] - 5, lengths[1][1]), (lengths[2][0] + 5, lengths[2][1]), lengths[3]]
    yield _member_part(sections, shifted)[0], data
    shifted = [(lengths[0][0] + 3, lengths[0][1]), (lengths[1][0] - 3, lengths[1][1]), *lengths[2:]]
    yield _member_part(sections, shifted)[0], data
    yield _member_part(sections, [lengths[0], (lengths[1][0], lengths[1][1] - 1), *lengths[2:]])[0], data
    yield _member_part(sections, [lengths[0], (lengths[1][0], lengths[1][1] + 1), *lengths[2:]])[0], data
    yield _member_part(sections, [lengths[0], lengths[1], (lengths[2][0], 100_000), lengths[3]])[0], data
    yield _member_part(sections, [*lengths[:3], (lengths[3][0], 1)])[0], data
    yield document, _flipped(data, lengths[0][0] + 12)
    yield document, _flipped(data, lengths[0][0] + lengths[1][0] - 6)
    yield document, _flipped(data, lengths[0][0] + lengths[1][0] - 2)
    yield document, data[: lengths[0][0]] + b"\xff" * (len(data) - lengths[0][0])


def _inflated(part: row_delta.Part, path: Path) -> tuple:
    """What `row_delta._Inflating` reads from the part at `path`, `INFLATE_READ_BYTES` at a time, as `part_stream`'s reader asks it for a buffer's worth: `("bytes", sha256, length)` or `("raised", class, message)`."""
    plain = bytearray()
    buffer = bytearray(row_delta.INFLATE_READ_BYTES)
    with open(path, "rb") as handle:
        raw = row_delta._Inflating(handle.fileno(), part)
        try:
            while count := raw.readinto(buffer):
                plain += buffer[:count]
        except row_delta.HeadError as error:
            return ("raised", type(error).__name__, str(error))
    return ("bytes", hashlib.sha256(plain).hexdigest(), len(plain))


def _open_parts_cases() -> Iterator[tuple[dict, bool, dict[str, bytes | None]]]:
    """The generated `open-parts` heads, each with `sha256` and the bytes at each part's name, None for a missing part: every part in place, with and without the sha256 check, a part missing, a part of another size, and a part of the head's size whose bytes hash to another sha256."""
    output, generation = ROW_DELTA_OUTPUT, ROW_DELTA_GENERATION
    names = [
        row_delta.base_name(output, generation),
        row_delta.segment_name(output, generation, 1),
        row_delta.segment_name(output, generation, 2),
    ]
    files = {
        names[0]: _gz(b"h\n", b"a\n"),
        names[1]: _place_segment(1, [b"+\t0\t\tb\n"]),
        names[2]: _place_segment(2, []),
    }
    parts = [
        _part_document(name, size=len(data), sha256=hashlib.sha256(data).hexdigest(), rows=1, rows_bytes=2)
        for name, data in files.items()
    ]
    document = _head_document(base=parts[0], segments=parts[1:])
    for sha256 in (False, True):
        yield document, sha256, dict(files)
        yield document, sha256, {**files, names[1]: None}
        yield document, sha256, {**files, names[0]: None, names[2]: None}
        yield document, sha256, {**files, names[2]: files[names[2]] + b"x"}
        yield document, sha256, {**files, names[1]: _flipped(files[names[1]], 4)}


def _row_delta_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `row-delta-read` stage's generated lines (the module docstring), each read by Python's readers over its files in a scratch directory under the dump."""
    blobs = source.out / ROW_DELTA_BLOBS
    output, generation = ROW_DELTA_OUTPUT, ROW_DELTA_GENERATION
    with tempfile.TemporaryDirectory(prefix="row-delta-read-", dir=source.out) as held:
        scratch = Path(held)
        yield _head_line("generated", _case_directory(scratch, {}), output)
        for raw in _head_cases():
            yield _head_line(
                "generated", _case_directory(scratch, {row_delta.head_name(output): raw}), output
            )
        for inputs in _segment_header_cases():
            yield _header_line("generated", inputs)
        for document, sha256, files in _open_parts_cases():
            directory = _case_directory(
                scratch, {name: data for name, data in files.items() if data is not None}
            )
            head = row_delta.parse_head(_head_bytes(document), output)
            yield from _opened_lines("generated", blobs, directory, head, sha256)
        for kind, number, data in _part_rows_cases():
            directory = _case_directory(scratch, {"part": data})
            with open(directory / "part", "rb") as handle:
                outcome = _rows_outcome(_part_items(kind, handle, output, generation, number))
            inputs = (kind, output, generation, number, _row_delta_blob(blobs, data))
            yield _row_delta_line("generated", "part-rows", inputs, outcome)
        for merge, parts in _merge_cases():
            directory = _case_directory(scratch, {str(number): data for number, data in enumerate(parts)})
            handles = [open(directory / str(number), "rb") for number in range(len(parts))]
            try:
                key = None if merge == row_delta.MERGE_PLACE else _tab_key
                outcome, keys = _merge_outcome(merge, handles, output, generation, key)
            finally:
                for handle in handles:
                    handle.close()
            digests = tuple(_row_delta_blob(blobs, data) for data in parts)
            yield _row_delta_line("generated", "merge", (merge, output, generation, digests, keys), outcome)
        for document, data in _inflating_cases():
            directory = _case_directory(scratch, {"part": data})
            outcome = _inflated(row_delta.Part.parse(document), directory / "part")
            inputs = (document, ("file", _row_delta_blob(blobs, data)))
            yield _row_delta_line("generated", "inflating", inputs, outcome)


ROW_DELTA_WRITE_STAGE = "row-delta-write"
# The `row-delta-write` stage's directory, beside its stage file, of the files its lines name by sha256.
ROW_DELTA_WRITE_BLOBS = "row-delta-write.blobs"
# The blob directory of the build whose `row-delta-write` stage records, for the length of that build or of its generated lines (`_row_delta_write_blobs_at`).
_ROW_DELTA_WRITE_BLOBS_AT: list[Path] = []
# The writers the `row-delta-write` recorders run themselves, taken before any build wraps them, and the signatures they bind a call's arguments by.
_PLACES_BETWEEN = row_delta.places_between
_ROW_LINE = row_delta.row_line
_ROW_LENGTH = row_delta.row_length
_SEGMENT_HEADER = row_delta.segment_header
_WRITES_BASE = row_delta.writes_base
_FOLD_DUE = row_delta.fold_due
_FOLD_FORCED = row_delta.fold_forced
_WRITE_SIGNATURES = {
    name: inspect.signature(getattr(row_delta, name))
    for name in (
        "places_between",
        "row_line",
        "row_length",
        "segment_header",
        "write_part",
        "write_segment",
        "commit",
        "remove_unlisted",
        "remove_staging",
        "writes_base",
        "fold_due",
        "fold_forced",
        "claim_fold",
        "with_segment",
    )
}


@contextlib.contextmanager
def _row_delta_write_blobs_at(directory: Path) -> Iterator[None]:
    _ROW_DELTA_WRITE_BLOBS_AT.append(directory)
    try:
        yield
    finally:
        _ROW_DELTA_WRITE_BLOBS_AT.pop()


def _bound(name: str, args: tuple, kwargs: dict) -> dict[str, Any]:
    bound = _WRITE_SIGNATURES[name].bind(*args, **kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


def _write_blob(data: bytes) -> str:
    return _row_delta_blob(_ROW_DELTA_WRITE_BLOBS_AT[-1], data)


def _held_before(path: Path) -> tuple:
    """What `path` holds before a call, as a line's input names it: `("missing",)`, or `("file", sha256)` with its bytes kept in the stage's blobs."""
    return _part_held(_ROW_DELTA_WRITE_BLOBS_AT[-1], path)


def _held_after(path: Path) -> tuple:
    """What `path` holds after a call, as a line's value names it: `("missing",)`, or `("file", sha256)`."""
    try:
        return ("file", hashlib.sha256(path.read_bytes()).hexdigest())
    except FileNotFoundError:
        return ("missing",)


def _staging(path: Path) -> Path:
    return path.with_name(path.name + output_sink.STAGING_SUFFIX)


def _identity(path: Path) -> tuple[int, int] | None:
    """A file's inode and modification time, or None when there is none, which tell a kept file from one put in its place."""
    try:
        found = os.stat(path)
    except FileNotFoundError:
        return None
    return found.st_ino, found.st_mtime_ns


def _entries(directory: Path) -> tuple[tuple[str, str], ...]:
    """The entries of `directory` by name, each with its kind: `link`, `dir` or `file`."""
    return tuple(
        (path.name, "link" if path.is_symlink() else "dir" if path.is_dir() else "file")
        for path in sorted(Path(directory).iterdir())
    )


def _row_value(row: row_delta.Row) -> tuple:
    return (row.op, row.place[0], row.place[1], row.payload)


def _lines_of(data: bytes) -> list[bytes]:
    """`data` cut after each newline, each line keeping its own."""
    pieces = data.split(b"\n")
    lines = [piece + b"\n" for piece in pieces[:-1]]
    if pieces[-1]:
        lines.append(pieces[-1])
    return lines


def _part_after(part: row_delta.Part, target: Path) -> tuple:
    return ("part", part.document(), _held_after(target), _held_after(_staging(target)))


def _raised_after(error: BaseException, target: Path) -> tuple:
    return ("raised", type(error).__name__, str(error), _held_after(target), _held_after(_staging(target)))


def _places_line(
    origin: str, low: row_delta.Place | None, high: row_delta.Place | None, count: int
) -> dict[str, object]:
    """A `places-between` line: the bounds and the count, and `("places", places)` or what `row_delta.places_between` raised."""
    try:
        value: tuple = ("places", tuple(_PLACES_BETWEEN(low, high, count)))
    except ValueError as error:
        value = ("raised", type(error).__name__, str(error))
    return _row_delta_line(origin, "places-between", (low, high, count), value)


def _row_line_line(origin: str, row: row_delta.Row) -> dict[str, object]:
    return _row_delta_line(origin, "row-line", _row_value(row), ("line", _ROW_LINE(row)))


def _row_length_line(origin: str, op: bytes, place: row_delta.Place, length: int) -> dict[str, object]:
    return _row_delta_line(
        origin, "row-length", (op, place, length), ("length", _ROW_LENGTH(op, place, length))
    )


def _segment_header_line(
    origin: str, output: str, generation: int, number: int, merge: str
) -> dict[str, object]:
    value = ("header", _SEGMENT_HEADER(output, generation, number, merge))
    return _row_delta_line(origin, "segment-header", (output, generation, number, merge), value)


def _decided_line(origin: str, form: str, inputs: tuple, decided: bool) -> dict[str, object]:
    return _row_delta_line(origin, form, inputs, ("decided", decided))


def _segment_line(
    origin: str, output: str, generation: int, number: int, held: tuple, rows: Sequence[tuple], value: tuple
) -> dict[str, object]:
    return _row_delta_line(origin, "write-segment", (output, generation, number, held, tuple(rows)), value)


def _removed(names: object) -> tuple[str, ...]:
    assert isinstance(names, list)
    return tuple(names)


def _commit_before(
    origin: str, directory: Path, head: row_delta.Head
) -> Callable[[object], dict[str, object]]:
    """A `commit` line's input, taken before the commit: the head's document, the directory's entries and what the head's path holds; its finisher takes the names the commit removed and adds what the head's path holds after it and whether the file was kept."""
    path = row_delta.head_path(directory, head.output)
    inputs = (head.document(), _entries(directory), _held_before(path))
    before = _identity(path)

    def finish(removed: object) -> dict[str, object]:
        kept = before is not None and before == _identity(path)
        value = ("committed", _removed(removed), _held_after(path), kept)
        return _row_delta_line(origin, "commit", inputs, value)

    return finish


def _removal_before(
    origin: str, form: str, directory: Path, inputs: tuple
) -> Callable[[object], dict[str, object]]:
    """A `remove-unlisted` or `remove-staging` line's input, `inputs` and the directory's entries taken before the call; its finisher takes the names removed."""
    inputs = (*inputs, _entries(directory))

    def finish(removed: object) -> dict[str, object]:
        return _row_delta_line(origin, form, inputs, ("removed", _removed(removed)))

    return finish


def _claim_before(origin: str, output: str) -> Callable[[object], dict[str, object]]:
    """A `claim-fold` line's input, taken before the claim: the output, the cycle's run, what the claim file in the cycle's directory holds and the output this process holds its claim for there; its finisher takes whether the output holds the fold and adds the claim file and the process's holder after it."""
    path = cycle_paths.CYCLE_VAR / cycle_paths.FOLD_CLAIM_NAME
    inputs = (
        output,
        os.environ.get(row_delta.CYCLE_RUN_ENV),
        _held_before(path),
        row_delta._process_claims.get(cycle_paths.CYCLE_VAR),
    )

    def finish(claimed: object) -> dict[str, object]:
        value = ("claimed", claimed, _held_after(path), row_delta._process_claims.get(cycle_paths.CYCLE_VAR))
        return _row_delta_line(origin, "claim-fold", inputs, value)

    return finish


def _places_cut(args: tuple, kwargs: dict) -> Finisher:
    arguments = _bound("places_between", args, kwargs)
    line = _places_line("run", arguments["low"], arguments["high"], arguments["count"])
    return lambda _result: line


def _row_lined(args: tuple, kwargs: dict) -> Finisher:
    line = _row_line_line("run", _bound("row_line", args, kwargs)["row"])
    return lambda _result: line


def _row_measured(args: tuple, kwargs: dict) -> Finisher:
    arguments = _bound("row_length", args, kwargs)
    line = _row_length_line("run", arguments["op"], arguments["place"], arguments["payload_length"])
    return lambda _result: line


def _segment_headed(args: tuple, kwargs: dict) -> Finisher:
    arguments = _bound("segment_header", args, kwargs)
    line = _segment_header_line(
        "run", arguments["output"], arguments["generation"], arguments["number"], arguments["merge"]
    )
    return lambda _result: line


def _part_written(args: tuple, kwargs: dict) -> Finisher:
    """A `row_delta.write_part` call: the target's state before it, and, once it returns, its gzip stream's plain bytes read back from the part, as one cut, and the rows its `fill` returned."""
    arguments = _bound("write_part", args, kwargs)
    name, gzip_name = arguments["name"], arguments["gzip_name"]
    target = Path(arguments["directory"]) / name
    held = _held_before(target)

    def finish(part: object) -> Mapping[str, object]:
        assert isinstance(part, row_delta.Part)
        plain = gzip.decompress(target.read_bytes())
        inputs = (
            name,
            gzip_name,
            held,
            _write_blob(plain),
            (len(plain),),
            (part.rows, part.rows_bytes),
            None,
        )
        return _row_delta_line("run", "write-part", inputs, _part_after(part, target))

    return finish


def _segment_written(args: tuple, kwargs: dict) -> Finisher:
    """A `row_delta.write_segment` call: the target's state before it, and, once it returns, the rows read back from the segment it wrote, past its header line."""
    arguments = _bound("write_segment", args, kwargs)
    output, generation, number = arguments["output"], arguments["generation"], arguments["number"]
    target = Path(arguments["directory"]) / row_delta.segment_name(output, generation, number)
    held = _held_before(target)

    def finish(part: object) -> Mapping[str, object]:
        assert isinstance(part, row_delta.Part)
        rows = []
        for line in _lines_of(gzip.decompress(target.read_bytes()))[1:]:
            op, anchor, fraction, payload = line.split(b"\t", 3)
            rows.append((op, int(anchor), fraction.decode("ascii"), payload))
        return _segment_line("run", output, generation, number, held, rows, _part_after(part, target))

    return finish


def _streamed_line(
    origin: str, name: str, header: bytes, held: tuple, data: str | None, ending: str, value: tuple
) -> dict[str, object]:
    """A `streamed-part` line: the part's name and header, its target's state before it opened, the blob of the bytes written past the header (None when it was never opened) and how it ended, `close` or `abandon`, with its part, none, or `abandoned` and what its target and staging file hold after."""
    return _row_delta_line(origin, "streamed-part", (name, header, held, data, ending), value)


def _streamed_after(target: Path) -> tuple[tuple, tuple]:
    return _held_after(target), _held_after(_staging(target))


def _streamed_closed(args: tuple, _kwargs: dict) -> Finisher | None:
    """A gzipped `row_delta.StreamedPart`'s `close`: its target's state as it closes, and the bytes it wrote past its header, read back from the part."""
    part = args[0]
    if type(part) is not row_delta.StreamedPart or not part._gzipped:
        return None
    target = part.directory / part.name
    held = _held_before(target)

    def finish(result: object) -> Mapping[str, object]:
        data = None
        value: tuple = ("none", *_streamed_after(target))
        if isinstance(result, row_delta.Part):
            data = _write_blob(gzip.decompress(target.read_bytes())[len(part._header) :])
            value = ("part", result.document(), *_streamed_after(target))
        return _streamed_line("run", part.name, part._header, held, data, "close", value)

    return finish


def _head_committed(args: tuple, kwargs: dict) -> Finisher:
    arguments = _bound("commit", args, kwargs)
    return _commit_before("run", Path(arguments["directory"]), arguments["head"])


def _unlisted_removed(args: tuple, kwargs: dict) -> Finisher:
    arguments = _bound("remove_unlisted", args, kwargs)
    head = arguments["head"]
    inputs = (arguments["output"], None if head is None else head.document())
    return _removal_before("run", "remove-unlisted", Path(arguments["directory"]), inputs)


def _staging_removed(args: tuple, kwargs: dict) -> Finisher:
    arguments = _bound("remove_staging", args, kwargs)
    inputs = (arguments["output"], arguments["links"])
    return _removal_before("run", "remove-staging", Path(arguments["directory"]), inputs)


def _share_decided(args: tuple, kwargs: dict) -> Finisher:
    arguments = _bound("writes_base", args, kwargs)
    inputs = (
        arguments["segment_rows_bytes"],
        arguments["base_rows_bytes"],
        arguments["every_row_rederived"],
    )
    line = _decided_line(
        "run", "writes-base", inputs, _WRITES_BASE(*inputs[:2], every_row_rederived=inputs[2])
    )
    return lambda _result: line


def _fold_decided(form: str, name: str, decide: Callable[..., bool]) -> Recorder:
    def record(args: tuple, kwargs: dict) -> Finisher:
        arguments = _bound(name, args, kwargs)
        head, cap = arguments["head"], arguments["cap"]
        line = _decided_line("run", form, (head.document(), cap), decide(head, cap))
        return lambda _result: line

    return record


def _fold_claimed(args: tuple, kwargs: dict) -> Finisher:
    return _claim_before("run", _bound("claim_fold", args, kwargs)["output"])


def _segment_added(args: tuple, kwargs: dict) -> Finisher:
    arguments = _bound("with_segment", args, kwargs)
    inputs = (arguments["head"].document(), arguments["part"].document(), arguments["stamps"])

    def finish(head: object) -> Mapping[str, object]:
        assert isinstance(head, row_delta.Head)
        return _row_delta_line("run", "with-segment", inputs, ("head", head.document()))

    return finish


def _write_head(
    output: str,
    generation: int,
    segments: Sequence[int] = (),
    *,
    base_bytes: int = 100,
    stamps: dict | None = None,
    merge: str = row_delta.MERGE_PLACE,
) -> row_delta.Head:
    """A head of `output` at `generation` whose base holds `base_bytes` row bytes and whose segments hold the row bytes `segments` lists."""

    def part(name: str, rows_bytes: int) -> row_delta.Part:
        return row_delta.Part(name, rows_bytes + 20, hashlib.sha256(name.encode()).hexdigest(), 3, rows_bytes)

    return row_delta.Head(
        output,
        generation,
        generation + len(segments),
        {"format": "stamp", "lines": ["a\tb"]} if stamps is None else stamps,
        part(row_delta.base_name(output, generation), base_bytes),
        tuple(
            part(row_delta.segment_name(output, generation, number), rows_bytes)
            for number, rows_bytes in enumerate(segments, start=1)
        ),
        merge,
    )


def _places_cases() -> Iterator[tuple[row_delta.Place | None, row_delta.Place | None, int]]:
    """The generated `places-between` bounds and counts: no bound, a base row's and a fraction's on either side, the same and adjacent anchors, counts on both sides of each power of 36, fractions past 128 bits, each refusal, and fractions `int(text, 36)` reads leniently or refuses."""
    long = "1" * 30
    yield from (
        (None, None, 0),
        (None, None, -2),
        (None, None, 1),
        (None, None, 2),
        (None, None, 34),
        (None, None, 35),
        (None, None, 36),
        (None, None, 1294),
        (None, None, 1295),
        ((0, ""), (1, ""), 1),
        ((0, ""), (1, ""), 7),
        ((0, ""), None, 3),
        (None, (0, ""), 3),
        (None, (-1, "5"), 2),
        ((5, ""), (5, "1"), 1),
        ((5, ""), (5, "1"), 35),
        ((5, ""), (5, "1"), 36),
        ((5, ""), (5, "01"), 2),
        ((5, "a"), (5, "b"), 1),
        ((5, "a"), (5, "a1"), 3),
        ((5, "z"), (5, "zz"), 2),
        ((5, "zz"), (6, ""), 2),
        ((5, "zzz"), None, 4),
        ((-1, "k"), (0, ""), 2),
        ((-1, ""), (-1, "1"), 1),
        ((5, long), (5, long[:-1] + "2"), 5),
        ((5, long), None, 3),
        ((5, "5"), (5, "5"), 1),
        ((5, "5"), (5, "4"), 1),
        ((6, ""), (5, "9"), 1),
        ((5, "5"), (5, ""), 1),
        (None, (-1, ""), 2),
        (None, (-2, "x"), 1),
        ((5, "1"), (5, "1_"), 1),
        ((5, "A"), (5, "a"), 1),
        ((5, "1"), (5, "Z"), 2),
        ((5, "-1"), (5, "0"), 3),
        ((5, " 1"), (5, "2"), 1),
        ((5, "+1"), (5, "2"), 1),
        ((5, "_1"), None, 1),
        ((5, "!"), None, 1),
        ((5, "1"), (5, "1!"), 1),
        ((5, "٣"), None, 2),
        ((5, "é"), None, 1),
        ((5, "1" * 4300), None, 1),
    )


def _row_cases() -> Iterator[row_delta.Row]:
    """The generated rows: each operation, anchors before the first row and past 32 bits, a fraction of one non-ASCII character, which `row_length` counts as one, and payloads with and without a newline and with tabs."""
    yield from (
        row_delta.Row(row_delta.UPSERT, (0, ""), b'{"key": "a"}\n'),
        row_delta.Row(row_delta.TOMBSTONE, (-1, "a"), b"t\n"),
        row_delta.Row(row_delta.UPSERT, (2**40, "zz1"), b""),
        row_delta.Row(row_delta.UPSERT, (3, "é"), b"p\n"),
        row_delta.Row(row_delta.TOMBSTONE, (7, ""), b"no newline"),
        row_delta.Row(row_delta.UPSERT, (-12345, "0i"), b"x\ty\n"),
    )


def _write_part_cases() -> (
    Iterator[tuple[str, str | None, bytes | None, bytes, tuple[int, ...], tuple[int, int], str | None]]
):
    """The generated `write-part` calls, each its name, gzip name, the bytes at its target or None, its plain bytes and the cuts its `fill` writes them in, what `fill` returns and the failure it raises after writing, or None: every kind of gzip name, nothing written, writes past the gzip writer's buffer and many small ones, a target replaced, and a `fill` that fails."""
    rows = b"".join(b'{"key": "%d"}\n' % index for index in range(6))
    small = b'{"format": "x"}\n' + rows
    bulk = _container_bulk()[: (1 << 20) + 7]
    base = row_delta.base_name(unit_cache.STORE_NAME, 3)
    whole = (len(small),)
    for gzip_name in (unit_cache._BASE_GZIP_NAME, None, "", "x.gz", "dir/x.ndjson.gz", "Šumava", "café"):
        yield base, gzip_name, None, small, whole, (6, len(rows)), None
    yield "part", None, None, b"", (), (0, 0), None
    yield "part", None, None, b"", (0, 0), (0, 0), None
    yield "part", None, None, bulk, (10, 600_000, 1, len(bulk) - 600_011), (9, len(bulk)), None
    small_cuts = (4096,) * (len(bulk) // 4096)
    yield "part", None, None, bulk, (*small_cuts, len(bulk) - sum(small_cuts)), (9, len(bulk)), None
    yield "part", None, b"old bytes", small, whole, (1, 2), None
    yield "part", None, b"old bytes", small, (5, len(small) - 5), (1, 2), "the fill failed"
    yield "part", None, None, small, whole, (1, 2), "the fill failed"


def _write_part_line(
    scratch: Path,
    name: str,
    gzip_name: str | None,
    held: bytes | None,
    plain: bytes,
    cuts: tuple[int, ...],
    returned: tuple[int, int],
    failure: str | None,
) -> dict[str, object]:
    directory = _case_directory(scratch, {} if held is None else {name: held})
    target = directory / name
    inputs = (name, gzip_name, _held_before(target), _write_blob(plain), cuts, returned, failure)

    def fill(stream: row_delta.Sink) -> tuple[int, int]:
        at = 0
        for cut in cuts:
            stream.write(plain[at : at + cut])
            at += cut
        if failure is not None:
            raise ValueError(failure)
        return returned

    try:
        part = row_delta.write_part(directory, name, fill, gzip_name=gzip_name)
    except ValueError as error:
        return _row_delta_line("generated", "write-part", inputs, _raised_after(error, target))
    return _row_delta_line("generated", "write-part", inputs, _part_after(part, target))


def _write_segment_cases() -> Iterator[tuple[str, int, int, bytes | None, list[row_delta.Row]]]:
    """The generated `write-segment` calls: rows before the first base row, tombstones, upserts at base places and between them, no row, rows out of place order and at one place twice, a target replaced and left, and a non-ASCII output."""
    good = [
        row_delta.Row(row_delta.UPSERT, (-1, "i"), b"first\n"),
        row_delta.Row(row_delta.TOMBSTONE, (0, ""), b'{"key": "0"}\n'),
        row_delta.Row(row_delta.UPSERT, (0, "5"), b"after zero\n"),
        row_delta.Row(row_delta.UPSERT, (2, ""), b"changed\n"),
        row_delta.Row(row_delta.UPSERT, (2, "a1"), b"after two\n"),
        row_delta.Row(row_delta.TOMBSTONE, (9, ""), b"gone\n"),
    ]
    disordered = [good[3], good[2]]
    twice = [good[3], row_delta.Row(row_delta.UPSERT, (2, ""), b"again\n")]
    output = unit_cache.STORE_NAME
    yield output, 2, 1, None, good
    yield output, 0, 17, None, good[:1]
    yield output, 2, 1, None, []
    yield output, 2, 1, None, disordered
    yield output, 2, 1, None, twice
    yield output, 2, 1, b"old bytes", good
    yield output, 2, 1, b"old bytes", disordered
    yield "Šumava", 5, 2, None, good


def _streamed_cases() -> Iterator[tuple[str, bytes, bytes | None, bytes | None, str]]:
    """The generated `streamed-part` writers, each its name and header, the bytes at its target or None, what it writes or None, and how it ends: never opened, opened by an empty write, rows, bytes past the gzip writer's buffer, a target replaced, and a part abandoned."""
    name = row_delta.segment_name(unit_index.INDEX_NAME, 2, 1)
    header = row_delta.segment_header(unit_index.INDEX_NAME, 2, 1, row_delta.MERGE_KEY)
    rows = b'+\t{"id": "a"}\n-\t{"id": "b"}\n'
    yield name, header, None, None, "close"
    yield name, header, None, b"", "close"
    yield name, header, None, rows, "close"
    yield name, header, None, _container_bulk()[: (1 << 20) + 7], "close"
    yield name, header, b"old bytes", rows, "close"
    yield name, header, b"old bytes", None, "close"
    yield "Šumava.0.1.delta", header, None, rows, "close"
    yield name, header, None, rows, "abandon"
    yield name, header, b"old bytes", rows, "abandon"
    yield name, header, None, None, "abandon"


def _streamed_generated(
    scratch: Path, name: str, header: bytes, before: bytes | None, data: bytes | None, ending: str
) -> dict[str, object]:
    directory = _case_directory(scratch, {} if before is None else {name: before})
    target = directory / name
    held = _held_before(target)
    part = row_delta.StreamedPart(directory, name, header, gzipped=True)
    if data is not None:
        part.write(data)
    if ending == "close":
        result = part.close()
        value: tuple = (
            ("none", *_streamed_after(target))
            if result is None
            else ("part", result.document(), *_streamed_after(target))
        )
    else:
        part.abandon()
        value = ("abandoned", *_streamed_after(target))
    digest = None if data is None else _write_blob(data)
    return _streamed_line("generated", name, header, held, digest, ending, value)


def _make_entries(directory: Path, entries: Iterable[tuple[str, str]]) -> None:
    for name, kind in entries:
        path = directory / name
        if kind == "dir":
            path.mkdir()
        elif kind == "link":
            path.symlink_to(os.devnull)
        else:
            path.write_bytes(b"")


def _part_entries(output: str) -> list[tuple[str, str]]:
    """The directory entries the generated `commit`, `remove-unlisted` and `remove-staging` lines run over, beside a head at generation 2 with one segment: its parts; other generations' and unlisted segments; names `row_delta._part_pattern` matches with other digits, Unicode ones among them, or does not match; staging files; another output's parts; a link, a plain file and a directory."""
    names = (
        "2.base",
        "2.1.delta",
        "1.base",
        "1.3.delta",
        "2.2.delta",
        "02.base",
        "٣.base",
        "2.0.delta",
        "x.base",
        "2.basex",
        "2.1.deltas",
        "2.base.partial",
        "2.1.delta.partial",
        "2.base.partial.partial",
        "٣.base.partial",
        "head.partial",
        "headx.partial",
    )
    entries = [(f"{output}.{name}", "file") for name in names]
    entries += [
        (f"{output}.3.base", "link"),
        (f"{output}.4.base.partial", "link"),
        ("other.1.base", "file"),
        ("other.head.partial", "file"),
        ("manifest.json", "file"),
        ("units", "dir"),
    ]
    return entries


def _write_heads() -> Iterator[row_delta.Head]:
    """The generated heads a commit puts in place, each at generation 2 with one segment: the unit store's, the units index's, one whose output a pattern would read as more than its name, one with nested and non-ASCII stamps, and one whose parts record what they were settled under."""
    yield _write_head(unit_cache.STORE_NAME, 2, (40,))
    yield _write_head(unit_index.INDEX_NAME, 2, (40,), merge=row_delta.MERGE_KEY)
    yield _write_head("a.b+c", 2, (40,))
    stamps = {"format": "x", "lines": ["a\tb", "é", 'q"'], "parts": {"p": 3}, "flag": True, "none": None}
    yield _write_head(unit_cache.STORE_NAME, 2, (40,), stamps=stamps)
    yield row_delta.settled_parts(
        _write_head(unit_cache.STORE_NAME, 2, (40,)), [("s1", {"qsPea": "k1"}), ("s2", {"qsPea": "k1"})]
    )


def _committed_generated(scratch: Path, head: row_delta.Head, state: str) -> dict[str, object]:
    directory = _case_directory(scratch, {})
    _make_entries(directory, _part_entries(head.output))
    path = row_delta.head_path(directory, head.output)
    if state == "equal":
        path.write_text(json.dumps(head.document(), indent=1) + "\n")
    elif state == "other":
        path.write_text("{}\n")
    finish = _commit_before("generated", directory, head)
    return finish(row_delta.commit(directory, head))


def _fold_cases() -> Iterator[row_delta.Head]:
    """The generated heads the fold's tests read: no segment, segments whose row bytes reach the base's or fall one short, and segment counts on both sides of the cap and of the cap and the overrun."""
    output = unit_cache.STORE_NAME
    yield _write_head(output, 2)
    yield _write_head(output, 2, (100,))
    yield _write_head(output, 2, (99,))
    yield _write_head(output, 2, (60, 40))
    for count in (4, 5, 15, 16, 20, 21, 32, 37):
        yield _write_head(output, 2, (1,) * count)


def _claim_file(run: str, output: str) -> bytes:
    return (json.dumps({"run": run, "output": output}) + "\n").encode()


def _claim_cases() -> Iterator[tuple[str | None, bytes | None, str | None]]:
    """The generated claims' states, each the cycle's run, the claim file's bytes or None, and the output this process holds its claim for or None: no run with no claim, this output's and another's, and a claim file a process ignores; this run's claim for this output and another, another run's, and claim files that do not read as a claim."""
    store, index = unit_cache.STORE_NAME, unit_index.INDEX_NAME
    yield from (
        (None, None, None),
        (None, None, store),
        (None, None, index),
        ("", None, index),
        (None, _claim_file("r1", index), None),
        ("r1", None, None),
        ("r1", None, index),
        ("r1", _claim_file("r1", store), None),
        ("r1", _claim_file("r1", index), None),
        ("r1", _claim_file("r0", index), None),
        ("r1", b"not json\n", None),
        ("r1", b"[]\n", None),
        ("r1", b"\xff\n", None),
        ("r1", b'{"run": "r1"}\n', None),
        ("r1", b'{"run": 1, "output": "x"}\n', None),
        ("r1", _claim_file("r1", "Šumava"), None),
        ("Šr", None, None),
    )


@contextlib.contextmanager
def _claim_state(directory: Path, run: str | None, holder: str | None) -> Iterator[None]:
    """Hold this process as a pass with `run` (none when None) whose cycle's directory is `directory` and whose claim there this process holds for `holder`, and put everything back on exit."""
    held_run, held_var = os.environ.get(row_delta.CYCLE_RUN_ENV), cycle_paths.CYCLE_VAR
    if run is None:
        os.environ.pop(row_delta.CYCLE_RUN_ENV, None)
    else:
        os.environ[row_delta.CYCLE_RUN_ENV] = run
    cycle_paths.CYCLE_VAR = directory
    if holder is not None:
        row_delta._process_claims[directory] = holder
    try:
        yield
    finally:
        row_delta._process_claims.pop(directory, None)
        cycle_paths.CYCLE_VAR = held_var
        if held_run is None:
            os.environ.pop(row_delta.CYCLE_RUN_ENV, None)
        else:
            os.environ[row_delta.CYCLE_RUN_ENV] = held_run


def _row_delta_write_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `row-delta-write` stage's generated lines (the module docstring), each made by Python's writers over files in a scratch directory under the dump."""
    with (
        _row_delta_write_blobs_at(source.out / ROW_DELTA_WRITE_BLOBS),
        tempfile.TemporaryDirectory(prefix="row-delta-write-", dir=source.out) as held,
    ):
        scratch = Path(held)
        for low, high, count in _places_cases():
            yield _places_line("generated", low, high, count)
        for row in _row_cases():
            yield _row_line_line("generated", row)
            yield _row_length_line("generated", row.op, row.place, len(row.payload))
        for output in (unit_cache.STORE_NAME, unit_index.INDEX_NAME, "Šumava", 'it\'s "q"\n'):
            for generation, number in ((0, 1), (7, 16), (2**40, 3)):
                for merge in (row_delta.MERGE_PLACE, row_delta.MERGE_KEY, row_delta.MERGE_PLAIN_KEY):
                    yield _segment_header_line("generated", output, generation, number, merge)
        for case in _write_part_cases():
            yield _write_part_line(scratch, *case)
        for output, generation, number, before, rows in _write_segment_cases():
            name = row_delta.segment_name(output, generation, number)
            directory = _case_directory(scratch, {} if before is None else {name: before})
            target = directory / name
            held_value = _held_before(target)
            try:
                value = _part_after(
                    row_delta.write_segment(directory, output, generation, number, rows), target
                )
            except ValueError as error:
                value = _raised_after(error, target)
            yield _segment_line(
                "generated", output, generation, number, held_value, [_row_value(row) for row in rows], value
            )
        for case in _streamed_cases():
            yield _streamed_generated(scratch, *case)
        for head in _write_heads():
            for state in ("missing", "equal", "other"):
                yield _committed_generated(scratch, head, state)
            entries = [*_part_entries(head.output), (row_delta.head_name(head.output), "file")]
            for listed in (None, head):
                directory = _case_directory(scratch, {})
                _make_entries(directory, entries)
                inputs = (head.output, None if listed is None else listed.document())
                finish = _removal_before("generated", "remove-unlisted", directory, inputs)
                yield finish(row_delta.remove_unlisted(directory, head.output, listed))
            for links in (True, False):
                directory = _case_directory(scratch, {})
                _make_entries(directory, entries)
                finish = _removal_before("generated", "remove-staging", directory, (head.output, links))
                yield finish(row_delta.remove_staging(directory, head.output, links=links))
        for inputs in (
            (0, 0, False),
            (0, 0, True),
            (1, 2, False),
            (1, 1, False),
            (2, 3, False),
            (5, 10, False),
            (6, 10, False),
            (2**62, 2**63 - 1, False),
            (-1, -3, False),
        ):
            yield _decided_line(
                "generated", "writes-base", inputs, _WRITES_BASE(*inputs[:2], every_row_rederived=inputs[2])
            )
        for head in _fold_cases():
            for cap in (None, row_delta.AUDIT_SEGMENT_CAP, 15, 0):
                yield _decided_line("generated", "fold-due", (head.document(), cap), _FOLD_DUE(head, cap))
                yield _decided_line(
                    "generated", "fold-forced", (head.document(), cap), _FOLD_FORCED(head, cap)
                )
        for run, claim, holder in _claim_cases():
            for output in (unit_cache.STORE_NAME, unit_index.INDEX_NAME):
                directory = _case_directory(
                    scratch, {} if claim is None else {cycle_paths.FOLD_CLAIM_NAME: claim}
                )
                with _claim_state(directory, run, holder):
                    finish = _claim_before("generated", output)
                    yield finish(row_delta.claim_fold(output))
        for head in (
            _write_head(unit_cache.STORE_NAME, 2),
            _write_head(unit_index.INDEX_NAME, 3, (5, 7), merge=row_delta.MERGE_KEY),
        ):
            part = row_delta.Part(
                row_delta.segment_name(head.output, head.generation, len(head.segments) + 1),
                9,
                "0" * 64,
                2,
                30,
            )
            for stamps in (dict(head.stamps), {"format": "moved", "nested": {"a": [1, None]}}):
                inputs = (head.document(), part.document(), stamps)
                result = row_delta.with_segment(head, part, stamps)
                yield _row_delta_line("generated", "with-segment", inputs, ("head", result.document()))


AUDIT_LAYOUT_STAGE = "audit-layout"
# The `audit-layout` stage's directory, beside its stage file, of the audit parts its generated lines name by sha256.
AUDIT_LAYOUT_BLOBS = "audit-layout.blobs"
# The key of the `audit-layout` line that records the build's own call.
LAYOUT_KEY = "layout"
# The path a generated audit's messages name.
LAYOUT_AUDIT_PATH = Path("out") / "m1" / oracle.AUDIT_NAME
# A generated row's last field this long crosses several of `audit_stream.PROBE_BYTES`' reads.
LAYOUT_LONG_FIELD = 3 * audit_stream.PROBE_BYTES + 17
# Configuration fields that are not UTF-8, each read with its invalid bytes replaced (`audit_stream._field`): a lone byte, overlong and surrogate forms, a cut sequence, one past U+10FFFF, a stray continuation, and a cut sequence inside a name.
LAYOUT_NOT_UTF8 = (
    b"\xff",
    b"\xf0\x80\x80",
    b"\xed\xa0\x80",
    b"\xe2\x82",
    b"\xc0\xaf",
    b"\xf4\x90\x80\x80",
    b"\x80\x80",
    b"def\xe2\x82ault",
    b"\xf0\x9f\x98",
)


def _layout_found(_args: tuple, _kwargs: dict) -> Finisher:
    """The build's `audit_layout` call over the audit it holds, recorded as the `audit-layout` boundary: what it finds (`audit.AuditLayout`) under `LAYOUT_KEY`."""
    return lambda result: Boundary(0, {}, [{"key": LAYOUT_KEY, "value": result}])


def _layout_line(blobs: Path, parts: Sequence[tuple[bytes, Sequence[row_delta.Member] | None]]) -> dict:
    """One generated `audit-layout` line: the audit's parts, each kept in `blobs` and read through a descriptor opened there, base first, with its member index or None, and what `audit.audit_layout` finds over them, every part plain when none has an index, or what it raises, `("raised", class, message)`. Its input is the path its messages name and each part's blob with its index as `(section, length, inflated)` tuples."""
    held = [(_blob_into(blobs, data), None if index is None else tuple(index)) for data, index in parts]
    indexes = tuple(index for _digest, index in held)
    descriptors = [os.open(blobs / digest, os.O_RDONLY) for digest, _index in held]
    try:
        value: object = audit.audit_layout(
            descriptors, LAYOUT_AUDIT_PATH, None if all(index is None for index in indexes) else indexes
        )
    except (ValueError, audit_stream.MemberError) as error:
        value = ("raised", type(error).__name__, str(error))
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    inputs = (
        str(LAYOUT_AUDIT_PATH),
        tuple(
            (digest, None if index is None else tuple(tuple(member) for member in index))
            for digest, index in held
        ),
    )
    digest = hashlib.sha1(compact(encode(inputs)).encode("ascii")).hexdigest()[:20]
    return {"key": f"generated:{digest}", "input": inputs, "value": value}


def _layout_rows(config: bytes, count: int, width: int = 3, shift: bytes = b"") -> bytes:
    """`count` rows of `config` in window order, each ending in a field of `width` bytes, each led by `shift` (a segment's `+\\t`)."""
    return b"".join(
        shift + config + f"\t{0xE650 + index:04X}\tcell\tentry\tbase\t".encode() + b"x" * width + b"\n"
        for index in range(count)
    )


def _layout_plain_cases() -> Iterator[bytes]:
    """The generated single-file audits: none, a header alone with and without its newline, headers that end in a carriage return, are not UTF-8 or are another header, sections in rank order and out of it, a section with another configuration's row at each place in it, rows past several line probes, a last row without its newline, blank and tabless lines, configurations that are not UTF-8 or hold a carriage return, and the mini audit."""
    header = ("\t".join(audit.AUDIT_HEADER) + "\n").encode()
    configs = [config.encode() for config in ACCEPTANCE_CONFIGS]
    default, ss03, ss04 = configs[0], configs[1], configs[2]
    yield b""
    yield header
    yield header[:-1]
    yield header[:-1] + b"\r\n" + _layout_rows(default, 2)
    yield header[:-1] + b"\r"
    yield header.replace(b"kinds", b"kin\xffds") + _layout_rows(default, 2)
    yield header.replace(b"kinds", b"kin\xe2\x82ds")
    yield b"\n" + _layout_rows(default, 2)
    yield header.replace(b"\tnew", b"\tnew\tmore")
    yield header.replace(b"\tnew", b"")
    yield header.replace(b"kinds", "k\u00efnds\x01\x7f'\"\\\u2028".encode())
    yield header[:-1] + b"\t" + b"y" * (2 * audit_stream.PROBE_BYTES) + b"\n"
    yield "\ufeff".encode() + header
    yield header + b"".join(_layout_rows(config, count) for config, count in zip(configs, (5, 1, 9, 3, 2, 4)))
    yield header + _layout_rows(default, 3) + _layout_rows(b"ss08", 2)
    yield header + _layout_rows(b"ss08", 2) + _layout_rows(b"zz", 2)
    yield header + _layout_rows(ss03, 3) + _layout_rows(default, 3)
    yield header + _layout_rows(ss04, 2) + _layout_rows(ss03, 2) + _layout_rows(configs[3], 1)
    for stray in range(12):
        rows = [_layout_rows(default, 1)] * 12
        rows[stray] = _layout_rows(ss03, 1)
        yield header + b"".join(rows) + _layout_rows(ss04, 3)
        rows[stray] = _layout_rows(ss04, 1)
        yield header + b"".join(rows) + _layout_rows(ss03, 1)
    yield (
        header
        + _layout_rows(default, 3)
        + _layout_rows(ss03, 3)
        + _layout_rows(default, 1)
        + _layout_rows(ss03, 3)
        + _layout_rows(ss04, 2)
    )
    yield (
        header
        + _layout_rows(default, 7, LAYOUT_LONG_FIELD)
        + _layout_rows(ss03, 1, LAYOUT_LONG_FIELD)
        + _layout_rows(configs[3], 30)
        + _layout_rows(configs[5], 3, LAYOUT_LONG_FIELD)
    )
    yield header + _layout_rows(default, 3) + _layout_rows(ss03, 2)[:-1]
    yield header + _layout_rows(default, 2) + _layout_rows(ss03, 1, LAYOUT_LONG_FIELD)[:-1]
    yield header + b"\n\n" + _layout_rows(default, 2)
    yield header + _layout_rows(default, 2) + b"\n"
    yield header + _layout_rows(default, 2) + b"default\n" + _layout_rows(ss03, 1)
    yield header + _layout_rows(default, 2) + b"ss03"
    yield header + _layout_rows(default, 2) + b"\t\t\n"
    for config in LAYOUT_NOT_UTF8:
        yield header + _layout_rows(default, 2) + _layout_rows(config, 2)
        yield header + _layout_rows(config, 2) + _layout_rows(default, 1)
    yield header + _layout_rows(b"default\r", 2)
    yield header + _layout_rows(default, 2) + _layout_rows(b"default\r", 1)
    yield (MINI / "audit.tsv").read_bytes()


def _layout_member(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=1, mtime=0)


def _layout_members(sections: Sequence[tuple[str, bytes]]) -> tuple[bytes, list[row_delta.Member]]:
    """A part held as gzip members, a section a member at level 1 with mtime 0, as `row_delta.MemberPart` writes one, and its member index."""
    members = [_layout_member(data) for _section, data in sections]
    index = [
        row_delta.Member(section, len(member), len(data))
        for (section, data), member in zip(sections, members)
    ]
    return b"".join(members), index


def _written_audit(
    directory: Path, passes: Sequence[Sequence[str]], capped: int = 0
) -> list[tuple[bytes, Sequence[row_delta.Member] | None]]:
    """The parts of an audit `oracle.AuditWriter` writes into `directory` over `passes`, each pass's rows in the audit's order, the first `capped` of them at a segment cap of one, so each folds what the last wrote: each part's bytes, base first, with its member index or None."""
    directory.mkdir(parents=True)
    held_cap = row_delta.AUDIT_SEGMENT_CAP
    try:
        for at, lines in enumerate(passes):
            row_delta.AUDIT_SEGMENT_CAP = 1 if at < capped else held_cap
            with oracle.AuditWriter(directory) as writer:
                writer.feed(("\n".join(lines) + "\n").encode("utf-8"))
    finally:
        row_delta.AUDIT_SEGMENT_CAP = held_cap
    head = row_delta.read_head(directory, oracle.AUDIT_NAME)
    assert head is not None, f"{directory} holds no audit head"
    return [((directory / part.name).read_bytes(), part.members) for part in head.parts]


def _plain_segments(
    parts: Sequence[tuple[bytes, Sequence[row_delta.Member] | None]],
) -> list[tuple[bytes, Sequence[row_delta.Member] | None]]:
    """`parts` with every segment held as gzip members rewritten as the plain lines its members inflate to, as #618's change 31 wrote every segment."""
    return [
        (gzip.decompress(data) if number and index is not None else data, None if number else index)
        for number, (data, index) in enumerate(parts)
    ]


def _layout_head_cases(scratch: Path) -> Iterator[list[tuple[bytes, Sequence[row_delta.Member] | None]]]:
    """The generated audits under a head, from the mini audit: as `oracle.AuditWriter` writes it over passes that drop rows and change others, a plain base with one and two segments held as gzip members, those segments plain, a folded base held as members with segments, and a lone folded base; then parts made by hand, a segment's members out of rank order or of one rank, a segment of its header alone, plain segments of upserts and tombstones in order, out of it and empty, and bases held as members whose header member fails each of `audit_stream._MemberReader`'s checks or holds another header."""
    header, *rows = (MINI / "audit.tsv").read_text(encoding="utf-8").splitlines()
    counts = collections.Counter(row.split("\t", 1)[0] for row in rows)
    sparse = min(counts, key=lambda config: (counts[config], config))
    first = [
        "\t".join((*fields[:2], fields[2] + ",first", *fields[3:])) if index % 5 == 1 else row
        for index, row in enumerate(rows)
        for fields in [row.split("\t")]
        if index % 7 != 3 and fields[0] != sparse
    ]
    middle = [
        "\t".join((*fields[:2], fields[2] + ",middle", *fields[3:])) if index % 11 == 5 else row
        for index, row in enumerate(rows)
        for fields in [row.split("\t")]
    ]
    one = _written_audit(scratch / "one", [first, rows])
    yield one
    two = _written_audit(scratch / "two", [first, middle, rows])
    yield two
    yield _plain_segments(one)
    yield _plain_segments(two)
    yield [two[0], _plain_segments(two)[1], two[2]]
    yield _written_audit(scratch / "folded", [first, middle, first, middle, rows], capped=3)
    yield _written_audit(scratch / "lone", [rows[:-1], rows[1:], rows], capped=3)
    base = (header + "\n" + "\n".join(rows) + "\n").encode("utf-8")
    segment_header = row_delta.segment_header(oracle.AUDIT_NAME, 0, 1, row_delta.MERGE_PLAIN_KEY)
    lines = _layout_rows(b"ss03", 2, shift=b"+\t")
    for sections in (
        [("", segment_header), ("ss03", lines), ("default", _layout_rows(b"default", 1, shift=b"+\t"))],
        [("", segment_header), ("ss08", _layout_rows(b"ss08", 1, shift=b"+\t")), ("zz", b"+\tzz\n")],
        [("", segment_header)],
        [("", segment_header), ("default", b"-\tdefault\tE650\n"), ("ss03", lines), ("ss10", b"")],
    ):
        data, index = _layout_members(sections)
        yield [(base, None), (data, index)]
        yield [(base, None), (data, index), (data, index)]
    for plain in (
        segment_header + _layout_rows(b"default", 2, shift=b"+\t") + b"-\tss03\tE650\n" + lines,
        segment_header + lines + _layout_rows(b"default", 2, shift=b"+\t"),
        segment_header + _layout_rows(b"default", 2, shift=b"+\t") + lines + b"-\tdefault\tE659\n",
        segment_header,
        segment_header[:-1],
        b"",
        b"+\tdefault\tE650\n" + lines,
    ):
        yield [(base, None), (plain, None)]
    header_line = (header + "\n").encode("utf-8")
    body = "\n".join(rows[:40]).encode("utf-8") + b"\n"
    data, index = _layout_members([("", header_line), ("default", body)])
    header_length, header_inflated = (index[0].length, index[0].inflated)
    rest = data[header_length:]
    yield [(data, index)]
    yield [(data, index), (segment_header + lines, None)]
    for recorded in (
        (header_length, header_inflated + 1),
        (header_length, header_inflated - 1),
        (header_length - 3, header_inflated),
        (header_length + 3, header_inflated),
    ):
        yield [(data, [row_delta.Member("", *recorded), *index[1:]])]
    yield [(data[: header_length - 5], index)]
    yield [(_flipped(data, header_length - 6), index)]
    yield [(_flipped(data, 12), index)]
    yield [(b"\xff" * header_length + rest, index)]
    for other in (header_line + header_line, header_line[:-1] + b"\r\n", b"kin\xffds\n", b""):
        member = _layout_member(other)
        yield [(member + rest, [row_delta.Member("", len(member), len(other)), *index[1:]])]


def _audit_layout_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `audit-layout` stage's generated lines (the module docstring), each found by `audit.audit_layout` over parts kept in the stage's blob directory, the head forms written in a scratch directory under the dump with the journal and the cycle directory redirected there (`_as_build_child`)."""
    blobs = source.out / AUDIT_LAYOUT_BLOBS
    for data in _layout_plain_cases():
        yield _layout_line(blobs, [(data, None)])
    with tempfile.TemporaryDirectory(prefix="audit-layout-", dir=source.out) as held:
        scratch = Path(held)
        with _as_build_child(scratch / "writer"):
            cases = list(_layout_head_cases(scratch / "audits"))
        for parts in cases:
            yield _layout_line(blobs, parts)


STREAM_STAGE = "stream"
# The `stream` stage's directory, beside its stage file, of the audit parts its generated lines name by sha256.
STREAM_BLOBS = "stream.blobs"
# The key of the `stream` line for how the build's walk ended.
STREAM_END_KEY = "end"
# The key of the `stream` line for the build's window ranges, each with its walk.
STREAM_RANGES_KEY = "ranges"
# The counts a generated audit's windows are split into (`audit_stream.window_ranges`): none, one, and either side of the key-only read's width.
STREAM_SPLITS = (0, 1, 2, 3, 6, 9)
# A big generated audit's last field this long puts its section past `audit_stream.READ_BYTES`.
STREAM_LONG_FIELD = 500
_STREAM_WORKLOAD = inspect.signature(audit.stream_workload)
_WINDOW_RANGES = inspect.signature(audit_stream.window_ranges)


def _stream_record(family: audit_stream.FamilyLines, window: str, rows: list) -> tuple:
    """A window's record in the `stream` stage: the window and its rows as `audit_stream.lockstep` yields them, its family lines (`FamilyLines.suffix`), whether it holds siblings (`sibling_window`), each row's signature key as `%016x` (`signature_key`), and the input key of its lines in order, as hex (`input_key`)."""
    suffix = family.suffix(audit_stream.parse_window(window))
    return (
        window,
        rows,
        suffix,
        audit_stream.sibling_window(rows),
        tuple(f"{audit_stream.signature_key(fields, suffix):016x}" for _index, fields, _line in rows),
        audit_stream.input_key((line for _index, _fields, line in rows), suffix).hex(),
    )


class _Walk:
    """`audit_stream.lockstep` over `sections` from `low` to `high`, each window with its rows in turn, ending at the first `OrderBreak` or `MemberError`, which `end` then holds as `("raised", class, message)`, or `("done",)` once the windows run out."""

    def __init__(
        self,
        descriptors: Sequence[int],
        sections: Sequence[audit_stream.Section],
        low: audit_stream.WindowKey | None = None,
        high: audit_stream.WindowKey | None = None,
    ) -> None:
        self._descriptors = descriptors
        self._sections = sections
        self._low = low
        self._high = high
        self.end: tuple = ("done",)

    def __iter__(self) -> Iterator[tuple[str, list]]:
        try:
            yield from audit_stream.lockstep(self._descriptors, self._sections, self._low, self._high)
        except (audit_stream.OrderBreak, audit_stream.MemberError) as error:
            self.end = ("raised", type(error).__name__, str(error))


def _walk_digest(walk: _Walk) -> tuple[int, str, tuple]:
    """How many windows a walk gives, the sha256 of their `(window, rows)` entries (`column_entry`), and how it ended."""
    digest = hashlib.sha256()
    count = 0
    for window, rows in walk:
        digest.update(column_entry((window, rows)))
        count += 1
    return count, digest.hexdigest(), walk.end


def _split_walks(
    descriptors: Sequence[int], sections: Sequence[audit_stream.Section], ranges: Iterable[tuple]
) -> tuple:
    """Each window range `(low, high)` with its walk (`_walk_digest`), as a key-only read process walks it."""
    return tuple((low, high, _walk_digest(_Walk(descriptors, sections, low, high))) for low, high in ranges)


def _stream_walked(args: tuple, kwargs: dict) -> Finisher:
    """The build's stream (`audit.stream_workload`) recorded as the `stream` boundary: the audit walked again in lockstep through the descriptors the build holds, over the sections and family keys the stream reads, each window's record (`_stream_record`) a unit under the `value` column, and how the walk ended under `STREAM_END_KEY`."""
    arguments = _STREAM_WORKLOAD.bind(*args, **kwargs).arguments
    descriptors, sections = arguments["descriptors"], arguments["sections"]
    family = audit_stream.FamilyLines(arguments["family_of"], arguments["family_keys"])

    def walk(ended: list[Mapping[str, object]]) -> Iterator[Mapping[str, object]]:
        stream = _Walk(descriptors, sections)
        for window, rows in stream:
            yield {"value": _stream_record(family, window, rows)}
        ended.append({"key": STREAM_END_KEY, "value": stream.end})

    return lambda _result: WalkedBoundary(("value",), walk)


def _ranges_split(args: tuple, kwargs: dict) -> Finisher:
    """The key-only read's `audit_stream.window_ranges` call, recorded in the `stream` boundary under `STREAM_RANGES_KEY`: its count and each range it gives with that range's walk (`_split_walks`)."""
    arguments = _WINDOW_RANGES.bind(*args, **kwargs).arguments
    descriptors, sections, count = arguments["descriptors"], arguments["sections"], arguments["count"]

    def finish(ranges: object) -> Boundary:
        assert isinstance(ranges, list), f"window_ranges returned {ranges!r:.80}"
        value = (count, _split_walks(descriptors, sections, ranges))
        return Boundary(0, {}, [{"key": STREAM_RANGES_KEY, "value": value}])

    return finish


def _dump_family(source: BuildSource) -> audit_stream.FamilyLines:
    """The family lines of the dump's build input (`BUILD_INPUT_FILE`), whose family keys the Rust side reads."""
    document = json.loads((source.out / BUILD_INPUT_FILE).read_text(encoding="ascii"))
    return audit_stream.FamilyLines(
        {int(value): name for value, name in document["family_of"]}, document["family_keys"]
    )


def _stream_line(
    blobs: Path,
    parts: Sequence[tuple[bytes, Sequence[row_delta.Member] | None]],
    family: audit_stream.FamilyLines,
    adjust: Callable[[list[audit_stream.Section]], list[audit_stream.Section]] | None = None,
) -> dict | None:
    """One generated `stream` line over an audit's parts, each kept in `blobs` and read through a descriptor opened there, base first, with its member index or None: the sections `audit.audit_layout` finds, through `adjust` when given, every window's record (`_stream_record`), how the walk ended, and for each of `STREAM_SPLITS` the ranges `audit_stream.window_ranges` gives with each range's walk (`_split_walks`), or what it raised. Its input is each part's blob with its index as `(section, length, inflated)` tuples, the sections and the counts. None for an audit the layout refuses or finds out of sorted sections, which no stream reads."""
    held = [(_blob_into(blobs, data), None if index is None else tuple(index)) for data, index in parts]
    indexes = tuple(index for _digest, index in held)
    descriptors = [os.open(blobs / digest, os.O_RDONLY) for digest, _index in held]
    try:
        try:
            layout = audit.audit_layout(
                descriptors, LAYOUT_AUDIT_PATH, None if all(index is None for index in indexes) else indexes
            )
        except ValueError, audit_stream.MemberError:
            return None
        if layout.sections is None:
            return None
        sections = tuple(layout.sections if adjust is None else adjust(layout.sections))
        stream = _Walk(descriptors, sections)
        records = tuple(_stream_record(family, window, rows) for window, rows in stream)
        splits: list[tuple[int, tuple]] = []
        for count in STREAM_SPLITS:
            try:
                ranges = audit_stream.window_ranges(descriptors, sections, count)
            except audit_stream.MemberError as error:
                splits.append((count, ("raised", "MemberError", str(error))))
            else:
                splits.append((count, ("ranges", _split_walks(descriptors, sections, ranges))))
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    inputs = (
        tuple(
            (digest, None if index is None else tuple(tuple(member) for member in index))
            for digest, index in held
        ),
        sections,
        STREAM_SPLITS,
    )
    digest = hashlib.sha1(compact(encode(inputs)).encode("ascii")).hexdigest()[:20]
    return {"key": f"generated:{digest}", "input": inputs, "value": (records, stream.end, tuple(splits))}


def _stream_row(config: str, window: str, new: bytes = b"n", baseline: bytes = b"b") -> bytes:
    return f"{config}\t{window}\tcell\tentry\t".encode() + baseline + b"\t" + new + b"\n"


def _stream_audit(rows: Iterable[tuple[str, str, bytes, bytes]]) -> bytes:
    """A single-file audit of `(config, window, baseline, new)` rows in sorted sections: by configuration rank, then window (`audit_stream.window_key`)."""
    configs: list[str] = list(ACCEPTANCE_CONFIGS)
    ordered = sorted(rows, key=lambda row: (configs.index(row[0]), audit_stream.window_key(row[1])))
    header = ("\t".join(audit.AUDIT_HEADER) + "\n").encode()
    return header + b"".join(
        _stream_row(config, window, new, baseline) for config, window, baseline, new in ordered
    )


# Rows over several configurations that share windows, some naming one (baseline, new) pair and some two, among windows that cite ligature families and a code point with no family.
STREAM_SIBLINGS: tuple[tuple[str, str, bytes, bytes], ...] = (
    ("default", "E650", b"b", b"a"),
    ("default", "0041:E650", b"b", b"a"),
    ("default", "E650:E653", b"b", b"a"),
    ("default", "E653:E67A", b"b", b"a"),
    ("default", "E651:E67A:E653", b"b", b"a"),
    ("ss03", "E650", b"b", b"a"),
    ("ss03", "E652", b"b", b"a"),
    ("ss03", "E650:E653", b"b", "néw ·Day".encode()),
    ("ss05", "E650:E653", b"c", b"a"),
    ("ss05", "E653:E67A", b"b", b"a"),
    ("ss10", "E653:E67A", b"b", b"z"),
    ("ss10", "E651:E67A:E653", b"b", b"a"),
)


def _stream_long(count: int) -> list[bytes]:
    """`count` `default` rows in window order, two code points a window, each ending in a field of `STREAM_LONG_FIELD` bytes."""
    windows = [
        f"{first:04X}:{second:04X}" for first in range(0xE650, 0xE67F) for second in range(0xE650, 0xE67F)
    ]
    return [_stream_row("default", window, b"x" * STREAM_LONG_FIELD) for window in windows[:count]]


def _stream_plain_cases() -> Iterator[bytes]:
    """The generated single-file audits the stream walks: windows shared across configurations with and without siblings; a window repeated, decreasing, or shorter after a longer one; a row with five or seven fields, a blank row, a carriage return and bytes that are not UTF-8 inside a section, the last row's cut short; windows that are not canonical; and a section past `audit_stream.READ_BYTES`, whole, with bytes that are not UTF-8 past its first read, and with a stray row there."""
    header = ("\t".join(audit.AUDIT_HEADER) + "\n").encode()
    yield _stream_audit(STREAM_SIBLINGS)
    yield _stream_audit(row for row in STREAM_SIBLINGS if row[0] != "default")

    def rows(*middle: bytes) -> bytes:
        return (
            header
            + _stream_row("default", "E650")
            + b"".join(middle)
            + _stream_row("default", "E659")
            + _stream_row("ss03", "E650")
        )

    yield rows(_stream_row("default", "E651"), _stream_row("default", "E651"))
    yield rows(_stream_row("default", "E652"), _stream_row("default", "E651"))
    yield header + _stream_row("default", "E650:E650") + _stream_row("default", "E651")
    yield rows(b"default\tE651\tcell\tentry\tb\n")
    yield rows(b"default\tE651\tcell\tentry\tb\tn\tmore\n")
    yield rows(b"\n")
    yield rows(_stream_row("default", "E651", b"x\ry"))
    yield rows(_stream_row("default", "E651", b"x\x01\x7f'\"\\y"))
    for cut in LAYOUT_NOT_UTF8:
        yield rows(_stream_row("default", "E651", b"x" + cut + b"y"))
        yield rows(_stream_row("default", "E651", cut))
    yield header + _stream_row("default", "E650") + _stream_row("default", "E651", b"x\xe2\x82")[:-1]
    yield header + _stream_row("default", "E650") + _stream_row("default", "E651")[:-1]
    for window in ("e650", "E65G", "E65", "É650", "E650:E65a", "E650:", "E650::E651", "E6500"):
        yield header + _stream_row("default", "E650") + _stream_row("default", "E651") + _stream_row(
            "ss03", "E650"
        ) + _stream_row("ss03", window)
    yield header + _stream_row("default", "E65")
    long = _stream_long(2209)
    yield header + b"".join(long)
    past = next(
        index
        for index, total in enumerate(itertools.accumulate(len(row) for row in long))
        if total > audit_stream.READ_BYTES + 3 * len(long[0])
    )
    yield header + b"".join(long[:past]) + long[past].replace(b"xxxx", b"x\xffxx", 1) + b"".join(
        long[past + 1 :]
    )
    yield header + b"".join(long[:past]) + long[past].replace(b"default", b"ss03", 1) + b"".join(
        long[past + 1 :]
    )


def _stream_truncated(sections: list[audit_stream.Section]) -> list[audit_stream.Section]:
    """The sections with the last one's end moved past its part's end, which a plain cursor reads as the audit ending inside it."""
    *kept, last = sections
    return [*kept, last._replace(end=last.end + 9)]


def _upsert(config: str, window: str, new: bytes = b"n") -> bytes:
    return b"+\t" + _stream_row(config, window, new)


def _tombstone(config: str, window: str) -> bytes:
    return f"-\t{config}\t{window}\n".encode()


def _stream_head(
    base: bytes, segments: Sequence[Sequence[tuple[str, bytes]]], members: bool
) -> list[tuple[bytes, Sequence[row_delta.Member] | None]]:
    """A plain base and one segment a list of `(config, lines)` sections, each segment's header line first, held as gzip members, a section a member (`_layout_members`), or plain."""
    parts: list[tuple[bytes, Sequence[row_delta.Member] | None]] = [(base, None)]
    for number, sections in enumerate(segments, 1):
        header = row_delta.segment_header(oracle.AUDIT_NAME, 0, number, row_delta.MERGE_PLAIN_KEY)
        if members:
            parts.append(_layout_members([("", header), *sections]))
        else:
            parts.append((header + b"".join(lines for _config, lines in sections), None))
    return parts


def _stream_head_cases() -> Iterator[list[tuple[bytes, Sequence[row_delta.Member] | None]]]:
    """The generated audits under a head the stream walks, each with its segments held as gzip members and plain where both apply: segments that supersede, remove, revive and add rows, and configurations held only in a segment; unknown configurations of one rank across three parts; segment lines that are neither an upsert nor a tombstone, a row of another configuration, and bytes that are not UTF-8; a segment section past `audit_stream.SEGMENT_READ_BYTES`; a member section that breaks the walk whole, whose index is off by one byte, or beside a member that does not inflate; and a base held as members whose section is past `audit_stream.READ_BYTES`."""
    header = ("\t".join(audit.AUDIT_HEADER) + "\n").encode()
    base = header + b"".join(
        _stream_row(config, window, b"a")
        for config, window in (
            ("default", "E650"),
            ("default", "E651"),
            ("default", "E652"),
            ("default", "E655"),
            ("ss04", "E650"),
            ("ss04", "E651"),
        )
    )
    first = [
        (
            "default",
            _upsert("default", "E651", b"b")
            + _tombstone("default", "E652")
            + _upsert("default", "E653", b"b")
            + _tombstone("default", "E659"),
        ),
        ("ss03", _upsert("ss03", "E650", b"b") + _upsert("ss03", "E651", b"b")),
    ]
    second = [
        (
            "default",
            _upsert("default", "E650", b"c")
            + _tombstone("default", "E651")
            + _upsert("default", "E652", b"c")
            + _tombstone("default", "E653"),
        ),
        ("ss03", _tombstone("ss03", "E650")),
        ("ss10", _upsert("ss10", "E651", b"c")),
    ]
    for members in (True, False):
        yield _stream_head(base, [first], members)
        yield _stream_head(base, [first, second], members)
        yield _stream_head(base, [second, first], members)
    unknown = header + _stream_row("default", "E650") + _stream_row("zz", "E650") + _stream_row("zz", "E652")
    yield _stream_head(
        unknown,
        [
            [("ss08", _upsert("ss08", "E650") + _upsert("ss08", "E651"))],
            [("zz", _upsert("zz", "E651") + _tombstone("zz", "E652"))],
        ],
        False,
    )
    for bad in (
        b"*\t" + _stream_row("default", "E651"),
        b"+\tdefault\tE651\tcell\tentry\tb\n",
        b"-\tdefault\tE651\textra\n",
        b"-\tdefault\n",
        b"garbage\n",
        b"\n",
        _upsert("ss03", "E651"),
        _upsert("default", "E651", b"x\xc0\xafy"),
        _upsert("default", "E651", b"x\ry"),
        _upsert("default", "E650"),
    ):
        lines = _upsert("default", "E650") + bad + _upsert("default", "E659")
        for members in (True, False):
            yield _stream_head(base, [[("default", lines)]], members)
    long = [b"+\t" + row for row in _stream_long(300)]
    for members in (True, False):
        yield _stream_head(base, [[("default", b"".join(long))]], members)
    past = next(
        index
        for index, total in enumerate(itertools.accumulate(len(row) for row in long))
        if total > audit_stream.SEGMENT_READ_BYTES + 2 * len(long[0])
    )
    broken = (
        b"".join(long[:past]) + long[past].replace(b"xxxx", b"x\xe2\x82x", 1) + b"".join(long[past + 1 :])
    )
    yield _stream_head(base, [[("default", broken)]], False)
    repeated = _upsert("default", "E650") + _upsert("default", "E651") + _upsert("default", "E651")
    segment = row_delta.segment_header(oracle.AUDIT_NAME, 0, 1, row_delta.MERGE_PLAIN_KEY)
    data, index = _layout_members([("", segment), ("default", repeated), ("ss03", _upsert("ss03", "E650"))])
    yield [(base, None), (data, index)]
    yield [
        (base, None),
        (data, [index[0], row_delta.Member("default", index[1].length, index[1].inflated + 1), index[2]]),
    ]
    yield [(base, None), (_flipped(data, len(data) - 6), index)]
    big = b"".join(_stream_long(2209))
    yield [_layout_members([("", header), ("default", big)])]
    yield [_layout_members([("", header), ("default", big), ("ss03", _stream_row("ss03", "E650"))])]


def _stream_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `stream` stage's generated lines (the module docstring), each walked by `audit_stream` over parts kept in the stage's blob directory with the family keys of the dump's build input: `audit-layout`'s generated audits (`_layout_plain_cases`, `_layout_head_cases`) and the stream's own (`_stream_plain_cases`, `_stream_head_cases`), and two with their last section cut short (`_stream_truncated`)."""
    blobs = source.out / STREAM_BLOBS
    family = _dump_family(source)
    lines: list[dict | None] = []
    for data in (*_layout_plain_cases(), *_stream_plain_cases()):
        lines.append(_stream_line(blobs, [(data, None)], family))
    for data in (_stream_audit(STREAM_SIBLINGS), (MINI / "audit.tsv").read_bytes()):
        lines.append(_stream_line(blobs, [(data, None)], family, _stream_truncated))
    with tempfile.TemporaryDirectory(prefix="stream-", dir=source.out) as held:
        scratch = Path(held)
        with _as_build_child(scratch / "writer"):
            cases = list(_layout_head_cases(scratch / "audits"))
        for parts in (*cases, *_stream_head_cases()):
            lines.append(_stream_line(blobs, parts, family))
    yield from (line for line in lines if line is not None)


SIGNATURE_STORE_STAGE = "signature-store"
# The `signature-store` stage's directory, beside its stage file, of the stores its lines name by sha256.
SIGNATURE_STORE_BLOBS = "signature-store.blobs"
# The blob directory of the build whose `signature-store` stage records, for the length of that build or of its generated lines (`_signature_store_blobs_at`).
_SIGNATURE_STORE_BLOBS_AT: list[Path] = []
_SIGNATURE_STORE_LOCK = threading.Lock()
# The calls on a `unit_cache.SignatureArrays` the stage follows into its trace, each by its method's name.
SIGNATURE_CALLS = (
    "stamps_hold",
    "prior_index",
    "mark_hit",
    "digest_of",
    "set_misses",
    "add_late",
    "hits",
    "misses",
    "unhit",
    "unchanged",
)
_SIGNATURE_METHODS = {
    name: inspect.signature(getattr(unit_cache.SignatureArrays, name)) for name in SIGNATURE_CALLS
}
_SIGNATURE_INIT = inspect.signature(unit_cache.SignatureArrays.__init__)
_SIGNATURE_LOAD = inspect.signature(unit_cache.SignatureArrays.load)
_SIGNATURE_WRITE = inspect.signature(unit_cache.write_signature_arrays)
_SIGNATURE_MISS_NOTE = inspect.signature(unit_cache.signature_miss_note)
# The entries past one read of the store's body (`unit_cache._SIGNATURE_READ_LINES`), and past the gzip writer's buffer, which the generated stores reach.
SIGNATURE_LONG_STORE = 13_000
SIGNATURE_LONG_WRITE = 7_000


@dataclass
class _SignatureTrace:
    """A `unit_cache.SignatureArrays` the stage follows: the store, held so that its `id` is not reused while the build runs; what it started from, `("empty",)` or `("load", held, environment)`; the calls made on it in order, each as its name and its arguments (`_signature_call`), beside their results; and the calls up to its last `unchanged`, which the build's write of it starts from."""

    arrays: unit_cache.SignatureArrays
    origin: tuple
    ops: list[tuple] = field(default_factory=list)
    results: list[object] = field(default_factory=list)
    settled: list[tuple] | None = None


# The stores the stage follows, by `id`, for the length of a build (`_signature_store_blobs_at`).
_SIGNATURE_TRACES: dict[int, _SignatureTrace] = {}


@contextlib.contextmanager
def _signature_store_blobs_at(directory: Path) -> Iterator[None]:
    _SIGNATURE_STORE_BLOBS_AT.append(directory)
    try:
        yield
    finally:
        _SIGNATURE_STORE_BLOBS_AT.pop()
        with _SIGNATURE_STORE_LOCK:
            _SIGNATURE_TRACES.clear()


def _signature_line(origin: str, form: str, inputs: tuple, value: object) -> dict[str, object]:
    """One `signature-store` line, keyed by its origin, its form and a digest of its input, with what Python gave as its `value`."""
    digest = hashlib.sha1(compact(encode(inputs)).encode("ascii")).hexdigest()[:20]
    return {"key": f"{origin}:{form}:{digest}", "form": form, "input": inputs, "value": value}


def _signature_held(path: Path) -> tuple:
    """What the store's path holds as a line names it: `("missing",)`, `("directory",)`, or `("file", sha256)` with its bytes kept in the stage's blobs."""
    if path.is_dir():
        return ("directory",)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ("missing",)
    with _SIGNATURE_STORE_LOCK:
        return ("file", _blob_into(_SIGNATURE_STORE_BLOBS_AT[-1], data))


def _store_columns(arrays: unit_cache.SignatureArrays | None) -> tuple:
    """A loaded store as a `load` line's value names it: `("none",)`, or `("columns", count, keys, digests, header)` with the sha256 of the keys as 8-byte big-endian words and of the digests."""
    if arrays is None:
        return ("none",)
    keys = hashlib.sha256(b"".join(key.to_bytes(8, "big") for key in arrays.keys)).hexdigest()
    return (
        "columns",
        len(arrays.keys),
        keys,
        hashlib.sha256(bytes(arrays.digests)).hexdigest(),
        arrays.header,
    )


def _store_lines(arrays: unit_cache.SignatureArrays) -> tuple:
    """The new store's entry lines (`SignatureArrays.lines`) as `("lines", count, sha256)` over them joined."""
    lines = list(arrays.lines())
    return ("lines", len(lines), hashlib.sha256(b"".join(lines)).hexdigest())


def _signature_call(name: str, values: Sequence[Any]) -> tuple:
    """A followed call as a trace names it: the method's name and its arguments, keys and digests as hex."""
    if name in ("prior_index", "digest_of"):
        return (name, f"{values[0]:016x}")
    if name == "mark_hit":
        return (name, int(values[0]))
    if name == "set_misses":
        keys, digests = values
        return (name, [f"{key:016x}" for key in keys], [str(digest) for digest in digests])
    if name == "add_late":
        return (name, f"{values[0]:016x}", bytes(values[1]).hex())
    if name == "stamps_hold":
        return (name, list(values[0]), values[1])
    return (name,)


def _signature_result(name: str, result: Any) -> object:
    return bytes(result).hex() if name == "digest_of" and result is not None else result


def _signature_followed(name: str) -> Recorder:
    """Follow one call on a store the stage follows into its trace; an `unchanged` call, which every build makes before it writes or keeps the store, records the trace so far as a `trace` line, its calls' results and the store's new lines."""
    method = _SIGNATURE_METHODS[name]

    def record(args: tuple, kwargs: dict) -> Finisher | None:
        arrays, *values = method.bind(*args, **kwargs).arguments.values()
        trace = _SIGNATURE_TRACES.get(id(arrays))
        if trace is None or trace.arrays is not arrays:
            return None
        call = _signature_call(name, values)

        def finish(result: object) -> Mapping[str, object] | None:
            with _SIGNATURE_STORE_LOCK:
                trace.ops.append(call)
                trace.results.append(_signature_result(name, result))
                if name != "unchanged":
                    return None
                trace.settled = list(trace.ops)
                inputs = (trace.origin, list(trace.ops))
                results = list(trace.results)
            return _signature_line("run", "trace", inputs, (results, _store_lines(arrays)))

        return finish

    return record


def _signatures_made(args: tuple, kwargs: dict) -> Finisher | None:
    """Follow a store made empty, `SignatureArrays()`; `SignatureArrays.load` makes the others."""
    bound = _SIGNATURE_INIT.bind(*args, **kwargs)
    if any(bound.arguments.get(name) is not None for name in ("keys", "digests", "header")):
        return None
    arrays = bound.arguments["self"]

    def finish(_result: object) -> None:
        with _SIGNATURE_STORE_LOCK:
            _SIGNATURE_TRACES[id(arrays)] = _SignatureTrace(arrays, ("empty",))
        return None

    return finish


def _signatures_loaded(args: tuple, kwargs: dict) -> Finisher:
    """A `SignatureArrays.load` call, with what the store's path held and the stamp's value, and the columns it loaded; a store it loads is followed from there."""
    bound = _SIGNATURE_LOAD.bind(*args, **kwargs)
    held = _signature_held(unit_cache.signature_store_path(bound.arguments["out_dir"]))
    environment = unit_cache._stamp_value(bound.arguments["environment"])

    def finish(result: object) -> Mapping[str, object]:
        if isinstance(result, unit_cache.SignatureArrays):
            with _SIGNATURE_STORE_LOCK:
                _SIGNATURE_TRACES[id(result)] = _SignatureTrace(result, ("load", held, environment))
        loaded = result if isinstance(result, unit_cache.SignatureArrays) else None
        return _signature_line("run", "load", (held, environment), _store_columns(loaded))

    return finish


def _stamp_fields(environment: fingerprint.EnvironmentStamp) -> tuple:
    """A stamp as a `miss-note` line names it: its value, its lines and its detail."""
    return (
        environment.value,
        tuple(environment.lines),
        tuple((label, tuple(lines)) for label, lines in environment.detail),
    )


def _signature_noted(args: tuple, kwargs: dict) -> Finisher:
    """A `unit_cache.signature_miss_note` call, with what the store's path held and the stamp, and the note it gave."""
    bound = _SIGNATURE_MISS_NOTE.bind(*args, **kwargs)
    inputs = (
        _signature_held(unit_cache.signature_store_path(bound.arguments["out_dir"])),
        _stamp_fields(bound.arguments["environment"]),
    )
    return lambda result: _signature_line("run", "miss-note", inputs, ("note", result))


def _signatures_writing(args: tuple, kwargs: dict) -> Finisher:
    """A `unit_cache.write_signature_arrays` call, on a pooled build's write thread or inline: the store's trace up to its last `unchanged`, the stamp's header fields (`unit_cache._stamp_header`), the stamps and what the store's path and its staging file held, with what each holds after."""
    bound = _SIGNATURE_WRITE.bind(*args, **kwargs)
    bound.apply_defaults()
    arrays = bound.arguments["signatures"]
    with _SIGNATURE_STORE_LOCK:
        trace = _SIGNATURE_TRACES.get(id(arrays))
        if trace is None or trace.arrays is not arrays:
            raise ValueError("the signature-store stage writes a store it did not follow from its making")
        ops = list(trace.settled if trace.settled is not None else trace.ops)
    path = unit_cache.signature_store_path(bound.arguments["out_dir"])
    stamps = bound.arguments["stamps"]
    inputs = (
        trace.origin,
        ops,
        unit_cache._stamp_header(bound.arguments["environment"]),
        None if stamps is None else dict(stamps),
        _signature_held(path),
        _signature_held(_staging(path)),
    )
    return lambda _result: _signature_line(
        "run", "write", inputs, ("written", _held_after(path), _held_after(_staging(path)))
    )


def _signature_entries(count: int, seed: str) -> list[tuple[int, str]]:
    """`count` entries, keys ascending, each key and digest drawn from sha256 of the seed and its index."""
    entries = {
        int.from_bytes(hashlib.sha256(f"{seed}:{index}".encode()).digest()[:8], "big"): hashlib.sha256(
            f"{seed}:digest:{index}".encode()
        ).hexdigest()
        for index in range(count)
    }
    return sorted(entries.items())


def _signature_body(entries: Iterable[tuple[int, str]]) -> bytes:
    return b"".join(f"{key:016x}\t{digest}\n".encode() for key, digest in entries)


def _signature_gzip(plain: bytes) -> bytes:
    return gzip.compress(plain, compresslevel=1, mtime=0)


def _signature_header(environment: object = "e", **fields: object) -> bytes:
    header: dict[str, object] = {"format": unit_cache.SIGNATURE_STORE_FORMAT, "environment": environment}
    header.update(fields)
    return (json.dumps(header) + "\n").encode()


def _signature_code(count: int, changed: Sequence[int] = (), seed: str = "") -> tuple[str, ...]:
    """A comparator closure's per-file lines, the files `changed` names carrying other digests."""
    return tuple(
        f"rebuild/review/m{index}.py\t{hashlib.sha256(f'{seed if index in changed else ''}{index}'.encode()).hexdigest()}"
        for index in range(count)
    )


def _signature_environment(
    code: Sequence[str] | None = None, drop: Sequence[str] = (), **labels: str
) -> fingerprint.EnvironmentStamp:
    """An ink-signature store's stamp in `unit_cache.signature_environment`'s shape: its format, the comparator's code over `code`'s lines, the before font and the after font's helpers, with `labels` changed or added and `drop` left out."""
    code_lines = tuple(_signature_code(6) if code is None else code)
    values = {
        "format": unit_cache.SIGNATURE_STORE_FORMAT,
        "comparator_code": fingerprint.digest_lines(code_lines),
        "before_font": "b" * 64,
        "after_helpers": "a" * 64,
    }
    values.update(labels)
    lines = tuple(f"{label}\t{digest}" for label, digest in values.items() if label not in drop)
    return fingerprint.EnvironmentStamp(lines=lines, detail=(("comparator_code", code_lines),))


def _signature_written(
    scratch: Path,
    environment: fingerprint.EnvironmentStamp | str,
    entries: Sequence[tuple[int, str]],
    stamps: Mapping[str, object] | None = None,
) -> bytes:
    """The store Python's writer writes for `entries` under `environment` and `stamps`."""
    directory = Path(tempfile.mkdtemp(dir=scratch))
    arrays = unit_cache.SignatureArrays()
    arrays.set_misses(array("Q", (key for key, _digest in entries)), [digest for _key, digest in entries])
    unit_cache.write_signature_arrays(directory, environment, arrays, stamps)
    return unit_cache.signature_store_path(directory).read_bytes()


def _signature_place(scratch: Path, data: bytes | str | None) -> Path:
    """A directory whose store path holds `data`: nothing for None, a directory for `"directory"`, or the bytes."""
    directory = Path(tempfile.mkdtemp(dir=scratch))
    path = unit_cache.signature_store_path(directory)
    if data == "directory":
        path.mkdir()
    elif isinstance(data, bytes):
        path.write_bytes(data)
    return directory


def _signature_raised(error: BaseException) -> tuple:
    return ("raised", type(error).__name__, str(error))


def _invalid_deflate() -> bytes:
    """A gzip member whose deflate data opens with a block of the reserved type, which `zlib` refuses as `zlib.error`."""
    return _signature_gzip(b"")[:10] + b"\x07\x00\x00\x00\x00"


def _signature_load_cases(scratch: Path) -> Iterator[tuple[bytes | str | None, str]]:
    """The stores the generated `load` lines read, each with the stamp's value it reads under: stores Python writes, short and past one read; headers of every kind `SignatureArrays.load` drops or passes; bodies of every kind of line it drops; and gzip files it reads, drops or raises on."""
    small = _signature_entries(5, "small")
    stamps = {unit_cache.AUDIT_IDENTITY_KEY: [3, 4], unit_cache.FAMILY_KEYS_KEY: "f"}
    written = _signature_written(scratch, "e", small, stamps)
    yield written, "e"
    yield written, "other"
    yield _signature_written(scratch, _signature_environment(), small), _signature_environment().value
    yield _signature_written(scratch, "e", _signature_entries(SIGNATURE_LONG_STORE, "long")), "e"
    yield _signature_written(scratch, "e", []), "e"
    yield None, "e"
    yield "directory", "e"
    body = _signature_body(small)
    for header in (
        _signature_header(),
        _signature_header().rstrip(b"\n"),
        b"[]\n",
        b'"e"\n',
        b"{}\n",
        _signature_header(format="ams-review-ink-signatures/1"),
        _signature_header(environment=5),
        _signature_header(environment=None),
        b"{\n",
        b'{"format": "\xff"}\n',
        b"\n",
        b"",
        b"\xef\xbb\xbf" + _signature_header(),
        b'{"format": "ams-review-ink-signatures/2", "environment": "e", "format": "x"}\n',
        b'{"environment": "e", "format": "ams-review-ink-signatures/2", "environment": "e"}\n',
    ):
        yield _signature_gzip(header), "e"
        yield _signature_gzip(header + body), "e"
    header = _signature_header()
    lines = body.splitlines(keepends=True)
    bad_bodies = [
        body.upper(),
        body[:-1],
        body + b"x",
        body + b"\n",
        body.replace(b"\n", b"\r\n"),
        lines[0][1:] + b"".join(lines[1:]),
        lines[1] + lines[0] + b"".join(lines[2:]),
        lines[0] + lines[0] + b"".join(lines[1:]),
        lines[0].replace(b"\t", b" ") + b"".join(lines[1:]),
        b"g" + lines[0][1:] + b"".join(lines[1:]),
        _signature_body(_signature_entries(unit_cache._SIGNATURE_READ_LINES, "chunk")) + b"x",
    ]
    for bad in bad_bodies:
        yield _signature_gzip(header + bad), "e"
    whole = _signature_gzip(header + body)
    yield b"not gzip at all", "e"
    yield whole[:-5], "e"
    yield whole[: len(whole) // 2], "e"
    yield whole + b"\x00" * 7, "e"
    yield whole + b"garbage", "e"
    yield _signature_gzip(header) + _signature_gzip(body), "e"
    yield _invalid_deflate(), "e"


def _signature_note_cases(
    scratch: Path,
) -> Iterator[tuple[bytes | str | None, fingerprint.EnvironmentStamp]]:
    """The stores the generated `miss-note` lines read, each with the stamp it reads under: no store, unreadable headers, a matching stamp, and every way a stamp's lines and detail record what moved."""
    environment = _signature_environment()
    small = _signature_entries(3, "note")
    yield None, environment
    yield "directory", environment
    yield b"not gzip", environment
    yield b"", environment
    yield _signature_gzip(b"[]\n"), environment
    yield _signature_gzip(b"{\n"), environment
    yield _invalid_deflate(), environment
    yield _signature_written(scratch, environment, small), environment
    moved = (
        _signature_environment(before_font="c" * 64),
        _signature_environment(code=_signature_code(6, changed=(2,), seed="x")),
        _signature_environment(code=_signature_code(6, changed=(0, 1, 2, 3, 4, 5), seed="x")),
        _signature_environment(code=_signature_code(4)),
        _signature_environment(code=_signature_code(8, changed=(7,), seed="y")),
        _signature_environment(drop=("after_helpers",)),
        _signature_environment(extra="1"),
        _signature_environment(
            before_font="c" * 64,
            after_helpers="d" * 64,
            **{f"label{index}": str(index) for index in range(9)},
        ),
        "e",
    )
    for recorded in moved:
        yield _signature_written(scratch, recorded, small), environment
    detail = {"comparator_code": list(_signature_code(6, changed=(1,), seed="z"))}
    lines = list(_signature_environment(before_font="c" * 64, comparator_code="9" * 64).lines)
    for fields in (
        {},
        {"environment_lines": "format\tx"},
        {"environment_lines": {"format": "x"}},
        {"environment_lines": [5, None, {"a": 1}, ["x\ty"], True, "plain", "a\tb\tc", "é\t1"]},
        {"environment_lines": lines},
        {"environment_lines": lines, "environment_detail": detail},
        {"environment_lines": lines[:1] + lines[2:], "environment_detail": detail},
        {"environment_lines": lines, "environment_detail": []},
        {"environment_lines": lines, "environment_detail": {}},
        {"environment_lines": lines, "environment_detail": {"comparator_code": "x"}},
        {"environment_lines": lines, "environment_detail": {"comparator_code": ["notab", 7]}},
        {"environment_lines": [], "environment_detail": detail},
    ):
        yield _signature_gzip(_signature_header(environment="x", **fields)), environment


def _signature_traces(scratch: Path) -> Iterator[tuple[bytes | None, str, list[tuple]]]:
    """The generated traces: each the store it starts from, None for an empty one, the stamp's value it loads under, and its calls, which reach every method's cases: keys at both ends of the range, absent keys between and beyond the prior ones, misses out of order and repeated, late misses repeated and equal to a miss or a prior key, the store left unchanged and changed, and headers whose stamps hold and fail by each of Python's `==` cases."""
    entries = [(0, "0" * 64), *_signature_entries(4, "trace"), (2**64 - 1, "f" * 64)]
    keys = [key for key, _digest in entries]
    store = _signature_written(scratch, "e", entries)

    def word(key: int) -> str:
        return f"{key:016x}"

    def digest(index: int) -> str:
        return hashlib.sha256(f"trace-miss:{index}".encode()).hexdigest()

    between = [keys[1] + 1, keys[2] + 1, keys[3] - 1, keys[4] + 1]
    yield (
        None,
        "e",
        [
            ("hits",),
            ("misses",),
            ("unhit",),
            ("unchanged",),
            ("digest_of", word(5)),
            ("prior_index", word(5)),
            (
                "set_misses",
                [word(9), word(5), word(9), word(7)],
                [digest(0), digest(1), digest(2), digest(3)],
            ),
            ("digest_of", word(9)),
            ("digest_of", word(7)),
            ("digest_of", word(6)),
            ("add_late", word(3), digest(4)),
            ("add_late", word(3), digest(5)),
            ("add_late", word(5), digest(6)),
            ("misses",),
            ("unchanged",),
            ("stamps_hold", [], ""),
        ],
    )
    yield (
        store,
        "e",
        [
            *(("prior_index", word(key)) for key in [*keys, *between]),
            ("unchanged",),
            ("digest_of", word(keys[0])),
            ("digest_of", word(keys[5])),
            ("digest_of", word(between[0])),
            ("hits",),
            ("unhit",),
            *(("mark_hit", position) for position in range(1, 5)),
            ("mark_hit", 2),
            ("hits",),
            ("unhit",),
            ("unchanged",),
            ("set_misses", [], []),
            ("unchanged",),
            ("add_late", word(between[1]), digest(7)),
            ("unchanged",),
            ("misses",),
        ],
    )
    yield (
        store,
        "e",
        [
            ("mark_hit", 0),
            ("mark_hit", 2),
            ("mark_hit", 5),
            (
                "set_misses",
                [word(between[2]), word(keys[2]), word(keys[1]), word(between[0]), word(keys[4])],
                [digest(index) for index in range(10, 15)],
            ),
            ("add_late", word(between[2]), digest(15)),
            ("add_late", word(keys[3]), digest(16)),
            ("add_late", word(between[3]), digest(17)),
            ("add_late", word(between[3]), digest(18)),
            ("add_late", word(1), digest(19)),
            ("digest_of", word(keys[1])),
            ("digest_of", word(between[2])),
            ("unchanged",),
        ],
    )
    for identity, family in (
        ([3, 4], "f"),
        ([True, 4], "f"),
        ([[3], 4], "f"),
        ("3,4", "f"),
        ([3, 4], 7),
        (None, "f"),
        ([3, 4], None),
    ):
        fields: dict[str, object] = {}
        if identity is not None:
            fields[unit_cache.AUDIT_IDENTITY_KEY] = identity
        if family is not None:
            fields[unit_cache.FAMILY_KEYS_KEY] = family
        data = _signature_gzip(_signature_header(**fields) + _signature_body(entries))
        yield (
            data,
            "e",
            [
                ("stamps_hold", [3, 4], "f"),
                ("stamps_hold", [1, 4], "f"),
                ("stamps_hold", [3, 4, 5], "f"),
                ("stamps_hold", [3], "f"),
                ("stamps_hold", [3, 4], "g"),
            ],
        )
    long = _signature_entries(SIGNATURE_LONG_WRITE, "trace-long")
    yield (
        None,
        "e",
        [("set_misses", [word(key) for key, _digest in long], [digest for _key, digest in long])],
    )


def _signature_apply(arrays: unit_cache.SignatureArrays, call: tuple) -> object:
    """Make one trace call on `arrays`, as `_signature_result` records its result."""
    name, *values = call
    if name in ("prior_index", "digest_of"):
        arguments: list[object] = [int(values[0], 16)]
    elif name == "set_misses":
        arguments = [array("Q", (int(key, 16) for key in values[0])), values[1]]
    elif name == "add_late":
        arguments = [int(values[0], 16), bytes.fromhex(values[1])]
    else:
        arguments = list(values)
    return _signature_result(name, getattr(arrays, name)(*arguments))


def _signature_traced(
    scratch: Path, data: bytes | None, environment: str, calls: Sequence[tuple]
) -> tuple[unit_cache.SignatureArrays, tuple, list[object]]:
    """A trace made: its store, loaded from `data` or empty, its origin as a line names it, and its calls' results."""
    if data is None:
        arrays: unit_cache.SignatureArrays | None = unit_cache.SignatureArrays()
        origin: tuple = ("empty",)
    else:
        directory = _signature_place(scratch, data)
        held = _signature_held(unit_cache.signature_store_path(directory))
        arrays = unit_cache.SignatureArrays.load(directory, environment)
        origin = ("load", held, environment)
    assert arrays is not None, "a generated trace starts from a store that loads"
    return arrays, origin, [_signature_apply(arrays, call) for call in calls]


def _signature_writes(
    scratch: Path,
) -> Iterator[
    tuple[
        bytes | None, list[tuple], fingerprint.EnvironmentStamp | str, dict | None, bytes | None, bytes | None
    ]
]:
    """The generated writes: each a trace's store and calls, the stamp, the stamps, and what the store's path and its staging file hold before: every prior entry hit beside a miss, under a stamp with its lines and one as a bare value, with and without stamps, stamps that take the header's own keys, a staging file left behind, no store before, an empty store, and a store past the gzip writer's buffer."""
    entries = _signature_entries(6, "write")
    store = _signature_written(scratch, "e", entries)
    environment = _signature_environment()
    stamps = {
        unit_cache.AUDIT_IDENTITY_KEY: [7, 1_700_000_000_123_456_789],
        unit_cache.FAMILY_KEYS_KEY: "f" * 64,
    }
    hit_all = [("mark_hit", position) for position in range(len(entries))]
    miss = [("set_misses", ["0000000000000001"], [hashlib.sha256(b"write-miss").hexdigest()])]
    calls = [*hit_all, *miss, ("unchanged",)]
    yield store, calls, environment, stamps, store, None
    yield store, calls, environment, None, None, None
    yield store, calls, "e", stamps, store, b"left behind"
    yield store, [*calls[:3], ("add_late", "00000000000000ff", "ab" * 32)], "e", {
        "environment": "over",
        "format": "other",
        "z": 1,
    }, None, None
    yield None, [("unchanged",)], environment, stamps, None, None
    long = _signature_entries(SIGNATURE_LONG_WRITE, "write-long")
    yield (
        None,
        [("set_misses", [f"{key:016x}" for key, _digest in long], [digest for _key, digest in long])],
        environment,
        stamps,
        None,
        None,
    )


def _signature_store_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `signature-store` stage's generated lines (the module docstring), each made by Python's store over files in a scratch directory under the dump."""
    with (
        _signature_store_blobs_at(source.out / SIGNATURE_STORE_BLOBS),
        tempfile.TemporaryDirectory(prefix="signature-store-", dir=source.out) as held,
    ):
        scratch = Path(held)
        for data, environment in _signature_load_cases(scratch):
            directory = _signature_place(scratch, data)
            stored = _signature_held(unit_cache.signature_store_path(directory))
            try:
                value = _store_columns(unit_cache.SignatureArrays.load(directory, environment))
            except zlib.error as error:
                value = _signature_raised(error)
            yield _signature_line("generated", "load", (stored, environment), value)
        for data, stamp in _signature_note_cases(scratch):
            directory = _signature_place(scratch, data)
            stored = _signature_held(unit_cache.signature_store_path(directory))
            try:
                value = ("note", unit_cache.signature_miss_note(directory, stamp))
            except zlib.error as error:
                value = _signature_raised(error)
            yield _signature_line("generated", "miss-note", (stored, _stamp_fields(stamp)), value)
        for data, environment, calls in _signature_traces(scratch):
            arrays, origin, results = _signature_traced(scratch, data, environment, calls)
            yield _signature_line(
                "generated", "trace", (origin, list(calls)), (results, _store_lines(arrays))
            )
        for data, calls, stamp, stamps, before, staging in _signature_writes(scratch):
            arrays, origin, _results = _signature_traced(scratch, data, "e", calls)
            directory = _signature_place(scratch, before)
            path = unit_cache.signature_store_path(directory)
            if staging is not None:
                _staging(path).write_bytes(staging)
            inputs = (
                origin,
                list(calls),
                unit_cache._stamp_header(stamp),
                stamps,
                _signature_held(path),
                _signature_held(_staging(path)),
            )
            unit_cache.write_signature_arrays(directory, stamp, arrays, stamps)
            yield _signature_line(
                "generated", "write", inputs, ("written", _held_after(path), _held_after(_staging(path)))
            )


SIGNATURE_STAGE = "signature"
_SHAPE_MISSES = inspect.signature(build._shape_misses)
_LATE_SHAPE = inspect.signature(build._LateShaper.__call__)


def _shaped_line(
    comparator: ink.InkComparator, origin: str, form: str, inputs: tuple[str, str], digest: str | None = None
) -> dict[str, object]:
    """One `signature` line, keyed by its origin, its form and a digest of its input: the text, for a `pair`, or the window, whose text `build.signature_text` gives, for a `late` or a `window`, and the configuration, with the signature `comparator` gives as its `signature` and that signature's digest (`ink.signature_digest`) as its `value`. A `digest` the build gave for the same input that is not that digest raises ValueError."""
    subject, config = inputs
    text = subject if form == "pair" else build.signature_text(subject)
    signature = comparator.signature(text, config)
    value = ink.signature_digest(signature)
    if digest is not None and digest != value:
        raise ValueError(f"the build gave {digest} for {inputs!r} and the signature digests to {value}")
    key = hashlib.sha1(compact(encode(inputs)).encode("ascii")).hexdigest()[:20]
    return {
        "key": f"{origin}:{form}:{key}",
        "form": form,
        "input": inputs,
        "signature": signature,
        "value": value,
    }


def _misses_shaped(args: tuple, kwargs: dict) -> Finisher:
    """A `build._shape_misses` call: each pair it shapes, with the digest it gives the pair, shaped again here for its signature."""
    arguments = _SHAPE_MISSES.bind(*args, **kwargs).arguments
    pairs = [tuple(pair) for pair in arguments["pairs"]]
    fonts = (arguments["before_font"], arguments["after_font"])

    def finish(result: object) -> list[Mapping[str, object]]:
        assert isinstance(result, tuple), f"_shape_misses returned {result!r:.80}"
        comparator = ink.InkComparator(*fonts)
        return [
            _shaped_line(comparator, "run", "pair", pair, digest)
            for pair, digest in zip(pairs, result[0], strict=True)
        ]

    return finish


def _late_shaped(args: tuple, kwargs: dict) -> Finisher:
    """A `build._LateShaper` call that shapes its window, which only a wrong skip of the key-only read gives: the window and the configuration, with the digest it gives them."""
    arguments = _LATE_SHAPE.bind(*args, **kwargs).arguments
    shaper = arguments["self"]
    shaped = shaper.shaped
    inputs = (arguments["window"], arguments["config"])

    def finish(result: object) -> Mapping[str, object] | None:
        if shaper.shaped == shaped:
            return None
        assert isinstance(result, bytes), f"_LateShaper returned {result!r:.80}"
        return _shaped_line(ink.InkComparator(*shaper._fonts), "run", "late", inputs, result.hex())

    return finish


def _source_windows(audit_path: Path) -> list[str]:
    """Every window of the audit at `audit_path` in window order, as the stream walks it (`audit_stream.lockstep`)."""
    parts = audit.AuditParts(audit_path)
    try:
        layout = audit.audit_layout(parts.descriptors, audit_path, parts.members)
        if layout.sections is None:
            raise SystemExit(f"{audit_path} is not in sorted sections ({layout.why}), so no stream walks it")
        return [window for window, _rows in audit_stream.lockstep(parts.descriptors, layout.sections)]
    finally:
        parts.close()


def _signature_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `signature` stage's generated lines: every window of the source's audit under each of `ACCEPTANCE_CONFIGS`, shaped over the before and after fonts of the dump's units init."""
    document = json.loads((source.out / BUILD_INPUT_FILE).read_text(encoding="ascii"))
    fonts = json.loads(Path(document["units_init"]).read_text(encoding="utf-8"))["fonts"]
    comparator = ink.InkComparator(fonts["before"], fonts["after"])
    for window in _source_windows(source.case.inputs.audit):
        for config in ACCEPTANCE_CONFIGS:
            yield _shaped_line(comparator, "generated", "window", (window, config))


KEY_ONLY_STAGE = "key-only"
# The `key-only` stage's directory, beside its stage file, of the signature stores its generated lines name by sha256.
KEY_ONLY_BLOBS = "key-only.blobs"
# The keys of the `key-only` stage's lines for the read, the count of misses shaped and the late shaper.
KEY_ONLY_READ_KEY = "read"
KEY_ONLY_SHAPED_KEY = "shaped"
KEY_ONLY_LATE_KEY = "late"
_FIND_MISSES = inspect.signature(build._find_misses)
_SHAPE_FOUND_MISSES = inspect.signature(build._shape_found_misses)
_LATE_RELEASE = inspect.signature(build._LateShaper.release)


def _read_value(found: build._KeyOnly) -> tuple:
    """The key-only read as the `read` line holds it: `("ran", processes, break)`, the break None when the read met none, or `("skipped",)` when the store's stamps held."""
    if not found.ran:
        return ("skipped",)
    return ("ran", found.processes, found.broken or None)


def _hit_positions(signatures: unit_cache.SignatureArrays) -> list[int]:
    """The positions of the prior entries the build has hit, ascending."""
    hit = signatures.hit
    return [
        position for position in range(len(signatures.keys)) if hit[position >> 3] & (1 << (position & 7))
    ]


def _miss_entry(found: build._KeyOnly, ordinal: int) -> tuple[str, int, str]:
    """One miss as the stage's unit holds it: its key as `%016x`, its section's index and its window."""
    return (f"{found.keys[ordinal]:016x}", int(found.sections[ordinal]), found.windows[ordinal])


def _miss_digest(signatures: unit_cache.SignatureArrays, key: int) -> str:
    """The digest the store's shaped misses hold for `key`, as hex."""
    keys = signatures.miss_keys
    position = bisect.bisect_left(keys, key)
    if position == len(keys) or keys[position] != key:
        raise ValueError(f"the shaped misses hold no key {key:016x}")
    return bytes(signatures.miss_digests[position * 32 : position * 32 + 32]).hex()


def _late_value(shaper: build._LateShaper) -> tuple:
    """The late shaper's count with its store's hits, misses and unhit entries and its new lines (`_store_lines`)."""
    signatures = shaper._signatures
    return (
        shaper.shaped,
        signatures.hits(),
        signatures.misses(),
        signatures.unhit(),
        _store_lines(signatures),
    )


def _misses_found(args: tuple, kwargs: dict) -> Finisher:
    """A `build._find_misses` call recorded in the `key-only` boundary: a unit a miss in miss order (`_miss_entry`), the hit prior entries (`_hit_positions`) and the read (`_read_value`)."""
    signatures = _FIND_MISSES.bind(*args, **kwargs).arguments["signatures"]

    def finish(found: object) -> Boundary:
        assert isinstance(found, build._KeyOnly), f"_find_misses returned {found!r:.80}"
        return Boundary(
            len(found.keys),
            {"value": functools.partial(_miss_entry, found)},
            [
                {"key": KEY_ONLY_READ_KEY, "value": _read_value(found)},
                column_line("hits", _hit_positions(signatures)),
            ],
        )

    return finish


def _found_misses_shaped(args: tuple, kwargs: dict) -> Finisher:
    """A `build._shape_found_misses` call recorded in the `key-only` boundary: the misses' digests in miss order (`_miss_digest`) and how many it shaped."""
    arguments = _SHAPE_FOUND_MISSES.bind(*args, **kwargs).arguments
    keys = arguments["key_only"].keys
    signatures = arguments["signatures"]

    def finish(result: object) -> Boundary:
        assert isinstance(result, tuple), f"_shape_found_misses returned {result!r:.80}"
        return Boundary(
            0,
            {},
            [
                column_line("digest", (_miss_digest(signatures, key) for key in keys)),
                {"key": KEY_ONLY_SHAPED_KEY, "value": result[0]},
            ],
        )

    return finish


def _late_released(args: tuple, kwargs: dict) -> Finisher:
    """A `build._LateShaper.release` call, once the stream has looked every row up, recorded in the `key-only` boundary (`_late_value`)."""
    value = _late_value(_LATE_RELEASE.bind(*args, **kwargs).arguments["self"])
    return lambda _result: Boundary(0, {}, [{"key": KEY_ONLY_LATE_KEY, "value": value}])


def _late_walk(
    descriptors: Sequence[int],
    sections: Sequence[audit_stream.Section],
    family: audit_stream.FamilyLines,
    late: build._LateShaper,
) -> None:
    """Look every row the stream keys up through `late`, as `audit.stream_workload` asks for digests: in each window of two or more units, each row's signature key under its configuration."""
    for window, rows in audit_stream.lockstep(descriptors, sections):
        if not audit_stream.sibling_window(rows):
            continue
        suffix = family.suffix(audit_stream.parse_window(window))
        for _index, fields, _line in rows:
            late(audit_stream.signature_key(fields, suffix), window, fields[0])


def _key_only_resolved(
    signatures: unit_cache.SignatureArrays,
    descriptors: Sequence[int],
    sections: Sequence[audit_stream.Section],
    family_of: Mapping[int, str],
    family_keys: Mapping[str, str],
    stamps: Mapping[str, object],
    fonts: tuple[Path, Path],
) -> tuple:
    """The load's signature resolution over `signatures` as a generated `key-only` line's value: the read, the hit prior entries and the misses (`build._find_misses`), each column as its count and digest (`column_digest`); then, unless the read broke, the misses' digests (`build._shape_found_misses`, serially), how many were shaped, and the late shaper's line once every row is looked up (`_late_walk`), or three Nones."""
    found = build._find_misses(descriptors, sections, signatures, family_of, family_keys, stamps)
    assert found is not None, "_find_misses skipped the merge"
    head = (
        _read_value(found),
        column_digest(_hit_positions(signatures)),
        column_digest(_miss_entry(found, ordinal) for ordinal in range(len(found.keys))),
    )
    if found.broken:
        return (*head, None, None, None)
    keys = found.keys
    shaped, _width = build._shape_found_misses(found, sections, signatures, *fonts, 1)
    digests = column_digest(_miss_digest(signatures, key) for key in keys)
    late = build._LateShaper(signatures, *fonts)
    try:
        _late_walk(descriptors, sections, audit_stream.FamilyLines(family_of, family_keys), late)
        value = _late_value(late)
    finally:
        late.release()
    return (*head, digests, shaped, value)


def _key_only_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `key-only` stage's generated lines, when the dump's prior corpus holds a store that loads under the build's signature stamp: that store with every other entry dropped, once with its stamps set to the build's, which skips the read and leaves the late shaper the dropped entries, and once with its audit identity moved, which runs the read over a partial store; each resolved over the dump's audit clone with the build's stamps (`_key_only_resolved`)."""
    document = json.loads((source.out / BUILD_INPUT_FILE).read_text(encoding="ascii"))
    environment = document["signature_environment"]["value"]
    prior = source.out / DUMP_PRIOR
    if unit_cache.SignatureArrays.load(prior, environment) is None:
        return
    header_line, _newline, body = gzip.decompress(
        unit_cache.signature_store_path(prior).read_bytes()
    ).partition(b"\n")
    header = json.loads(header_line)
    width = unit_cache._SIGNATURE_LINE_BYTES
    kept = b"".join(body[at : at + width] for at in range(0, len(body), 2 * width))
    stamps = {
        unit_cache.AUDIT_IDENTITY_KEY: document["audit"]["identity"],
        unit_cache.FAMILY_KEYS_KEY: document["family_keys_digest"],
    }
    family_of = {int(value): name for value, name in document["family_of"]}
    fonts_used = json.loads(Path(document["units_init"]).read_text(encoding="utf-8"))["fonts"]
    fonts = (Path(fonts_used["before"]), Path(fonts_used["after"]))
    description = json.loads((source.out / SOURCE_FILE).read_text(encoding="ascii"))
    audit_path = source.out / description["inputs"]["audit"]
    blobs = source.out / KEY_ONLY_BLOBS
    parts = audit.AuditParts(audit_path)
    try:
        layout = audit.audit_layout(parts.descriptors, audit_path, parts.members)
        if layout.sections is None:
            return
        with tempfile.TemporaryDirectory(prefix="key-only-", dir=source.out) as held:
            for label, written in (
                ("skipped", stamps),
                ("read", {**stamps, unit_cache.AUDIT_IDENTITY_KEY: []}),
            ):
                data = gzip.compress((json.dumps({**header, **written}) + "\n").encode() + kept, mtime=0)
                inputs = (label, _blob_into(blobs, data))
                directory = Path(tempfile.mkdtemp(dir=held))
                unit_cache.signature_store_path(directory).write_bytes(data)
                signatures = unit_cache.SignatureArrays.load(directory, environment)
                assert signatures is not None, f"the {label} store does not load"
                value = _key_only_resolved(
                    signatures,
                    parts.descriptors,
                    layout.sections,
                    family_of,
                    document["family_keys"],
                    stamps,
                    fonts,
                )
                key = hashlib.sha1(compact(encode(inputs)).encode("ascii")).hexdigest()[:20]
                yield {"key": f"generated:{key}", "input": inputs, "value": value}
    finally:
        parts.close()


COLUMNS_STAGE = "columns"
# The form of each collection the `columns` stage follows from its making, by class.
COLUMNS_FOLLOWED = {
    "StringTable": "string-table",
    "TuplePool": "tuple-pool",
    "MappingPool": "mapping-pool",
    "KeyIndex": "key-index",
}
# A followed call's name in a line, by its method's name where the two differ.
COLUMNS_OPS = {"__getitem__": "getitem", "__len__": "len"}
_COLUMNS_LOCK = threading.Lock()
_KEY_INDEX_INIT = inspect.signature(columns.KeyIndex.__init__)
# The methods the stage calls for values of its own, as `columns` defines them before any is wrapped: the window index's on a probe over the index's own columns, so a state's values are Python's without a call on the build's index, and a key index's size.
_WINDOW_RUNS = columns.WindowIndex.runs
_WINDOW_BYTES = columns.WindowIndex.nbytes
_KEY_INDEX_BYTES = columns.KeyIndex.nbytes


@dataclass
class _ColumnsTrace:
    """A `columns` collection the stage follows: the collection, held so that its `id` is not reused while the build runs; its name in the lines, `run:<form>:<serial>`; its form; the thread that made it, the only one whose calls on it the stage takes in order; and how many calls it has recorded."""

    held: object
    name: str
    form: str
    thread: int
    calls: int = 0


# The collections the stage follows, by `id`, and the window index states it has written a line for, by their columns' digests, for the length of a build (`_columns_following`).
_COLUMNS_TRACES: dict[int, _ColumnsTrace] = {}
_WINDOW_STATES: dict[tuple, str] = {}


@contextlib.contextmanager
def _columns_following() -> Iterator[None]:
    with _COLUMNS_LOCK:
        _COLUMNS_TRACES.clear()
        _WINDOW_STATES.clear()
    try:
        yield
    finally:
        with _COLUMNS_LOCK:
            _COLUMNS_TRACES.clear()
            _WINDOW_STATES.clear()


def _columns_digest(inputs: object) -> str:
    return hashlib.sha1(compact(encode(inputs)).encode("ascii")).hexdigest()[:20]


def _columns_value(value: object) -> object:
    """A call's argument or result as a `columns` line holds it: a mapping as a dict in its order, a byte buffer as bytes, an array as a list, and anything else as itself."""
    if isinstance(value, Mapping):
        return dict(value.items())
    if isinstance(value, bytearray | memoryview):
        return bytes(value)
    if isinstance(value, array):
        return value.tolist()
    return value


def _columns_raised(error: Exception) -> tuple:
    """A refused call's outcome: `("raised", "RepeatedKey", first, second)`, or the exception's class and message."""
    if isinstance(error, columns.RepeatedKey):
        return ("raised", "RepeatedKey", error.first, error.second)
    return ("raised", type(error).__name__, str(error))


def _columns_after(form: str, made: Any, result: object) -> object:
    """A followed call's value: what it returned beside the collection's size after it, the string table's `chars`, a tuple pool's `elements` and `sealed`, a mapping pool's `elements`, and a key index's result alone."""
    if form == "string-table":
        return (result, made.chars)
    if form == "tuple-pool":
        return (result, made.elements, made.sealed)
    if form == "mapping-pool":
        return (result, made.elements)
    return result


def _columns_line(name: str, form: str, index: int, call: tuple, value: object) -> dict[str, object]:
    return {"key": f"{name}:{index}", "form": form, "input": (name, index, call), "value": value}


def _columns_step(trace: _ColumnsTrace, call: tuple, value: object) -> dict[str, object]:
    """The line of a followed collection's next call; a call on another thread than the one that made it is refused, since the calls' order in the lines would not be the order they ran in."""
    with _COLUMNS_LOCK:
        if threading.get_ident() != trace.thread:
            raise RuntimeError(f"the columns stage follows {trace.name}, which a second thread calls")
        index = trace.calls
        trace.calls += 1
    return _columns_line(trace.name, trace.form, index, call, value)


def _columns_made(owner: str) -> Recorder:
    """Follow a collection from its making: a `new` line, a key index's with the bytes of its column it reads, its width and its count, valued `("built", nbytes)`."""
    form = COLUMNS_FOLLOWED[owner]

    def record(args: tuple, kwargs: dict) -> Finisher:
        made = args[0]
        call: tuple = ("new",)
        if form == "key-index":
            bound = _KEY_INDEX_INIT.bind(*args, **kwargs)
            width, count = bound.arguments["width"], bound.arguments["count"]
            call = ("new", bytes(bound.arguments["column"][: width * count]), width, count)

        def finish(_result: object) -> Mapping[str, object]:
            with _COLUMNS_LOCK:
                trace = _ColumnsTrace(made, f"run:{form}:{len(_COLUMNS_TRACES)}", form, threading.get_ident())
                _COLUMNS_TRACES[id(made)] = trace
            if form == "key-index":
                return _columns_step(trace, call, ("built", _KEY_INDEX_BYTES(made)))
            return _columns_step(trace, call, _columns_after(form, made, None))

        return finish

    return record


def _columns_called(name: str) -> Recorder:
    """One call on a followed collection, with its arguments and what it returned beside the collection's size after it (`_columns_after`)."""
    op = COLUMNS_OPS.get(name, name)

    def record(args: tuple, kwargs: dict) -> Finisher | None:
        made, *values = args
        trace = _COLUMNS_TRACES.get(id(made))
        if trace is None or trace.held is not made:
            return None
        if kwargs:
            raise ValueError(f"the columns stage records {op} called by position, not with {sorted(kwargs)}")
        call = (op, *map(_columns_value, values))
        return lambda result: _columns_step(
            trace, call, _columns_after(trace.form, made, _columns_value(result))
        )

    return record


def _window_state(origin: str, index: columns.WindowIndex) -> tuple[str, Mapping[str, object] | None]:
    """A window index's state as the stage names it, the digest of its ordinals and keys, beside the `window-index-state` line that holds them, valued what `packed`, `windows`, `runs` and `nbytes` give over them, the first time the state is seen in this build (None after that)."""
    ordinals, keys = index.ordinals, index.keys
    if isinstance(keys, array):
        fingerprint: tuple = (hashlib.sha1(ordinals).digest(), hashlib.sha1(keys).digest())
    else:
        fingerprint = (hashlib.sha1(ordinals).digest(), _columns_digest(list(keys)))
    with _COLUMNS_LOCK:
        known = _WINDOW_STATES.get(fingerprint)
    if known is not None:
        return known, None
    state = (ordinals.tolist(), list(keys))
    digest = _columns_digest(state)
    probe = columns.WindowIndex(ordinals, keys)
    value = (probe.packed, probe.windows, list(_WINDOW_RUNS(probe)), _WINDOW_BYTES(probe))
    with _COLUMNS_LOCK:
        _WINDOW_STATES[fingerprint] = digest
    line = {
        "key": f"{origin}:window-index-state:{digest}",
        "form": "window-index-state",
        "input": (digest, probe.packed, *state),
        "value": value,
    }
    return digest, line


def _window_result(name: str, result: Any) -> object:
    if name == "remapped":
        return (result.ordinals.tolist(), list(result.keys))
    return _columns_value(result)


def _window_line(origin: str, state: str, call: tuple, value: object) -> dict[str, object]:
    inputs = (state, call)
    return {
        "key": f"{origin}:window-index:{_columns_digest(inputs)}",
        "form": "window-index",
        "input": inputs,
        "value": value,
    }


def _window_called(name: str) -> Recorder:
    """One call on a window index, over its state as the call finds it (`_window_state`), with what it returned; `runs`, which returns a generator the build consumes, is valued the runs the stage takes from a probe over the same state."""

    def record(args: tuple, kwargs: dict) -> Finisher:
        index, *values = args
        if kwargs:
            raise ValueError(
                f"the columns stage records {name} called by position, not with {sorted(kwargs)}"
            )
        state, line = _window_state("run", index)
        call = (name, *map(_columns_value, values))
        runs = list(_WINDOW_RUNS(columns.WindowIndex(index.ordinals, index.keys))) if name == "runs" else None

        def finish(result: object) -> list[Mapping[str, object]]:
            value = runs if name == "runs" else _window_result(name, result)
            return [*([line] if line is not None else []), _window_line("run", state, call, value)]

        return finish

    return record


def _stateless_line(origin: str, form: str, inputs: object, value: object) -> dict[str, object]:
    return {
        "key": f"{origin}:{form}:{_columns_digest(inputs)}",
        "form": form,
        "input": inputs,
        "value": value,
    }


def _window_worded(args: tuple, kwargs: dict) -> Finisher:
    """A `columns.window_word` call, with the values it packs and the word or None it gave."""
    (values,) = (*args, *kwargs.values())
    inputs = list(values)
    return lambda result: _stateless_line("run", "window-word", inputs, result)


def _words_sorted(args: tuple, kwargs: dict) -> Finisher:
    """A `columns.sorted_by_word` call, with the words it sorts and the positions it gave."""
    (words,) = (*args, *kwargs.values())
    inputs = list(words)
    return lambda result: _stateless_line("run", "sorted-by-word", inputs, _columns_value(result))


def _columns_traced(
    name: str, form: str, make: Callable[[], Any], new: tuple, calls: Iterable[tuple]
) -> Iterator[Mapping[str, object]]:
    """A generated trace: a collection `make` makes, as its `new` line, then each call on it as a line, as the run's lines are (`_columns_made`, `_columns_called`), a refused call valued its refusal (`_columns_raised`) beside the size after it."""
    try:
        made = make()
    except (ValueError, columns.RepeatedKey) as error:
        yield _columns_line(name, form, 0, new, _columns_raised(error))
        return
    first = ("built", _KEY_INDEX_BYTES(made)) if form == "key-index" else _columns_after(form, made, None)
    yield _columns_line(name, form, 0, new, first)
    methods = {op: method for method, op in COLUMNS_OPS.items()}
    for index, call in enumerate(calls, 1):
        op: str = call[0]
        try:
            result = _columns_value(getattr(made, methods.get(op, op))(*call[1:]))
        except (ValueError, TypeError) as error:
            result = _columns_raised(error)
        yield _columns_line(name, form, index, call, _columns_after(form, made, result))


# The strings the generated string tables intern: the empty one, repeats, and text of one, two, three and four UTF-8 bytes a character.
COLUMNS_STRINGS = ("a", "b", "", "a", "é·Day", "\U0001f600", "b", "a b\tc\n", "\x00", "·", "\ue650\ue670")
# The tuples the generated tuple pools intern: the empty one, repeats, tuples that differ only in order or length, and tuples of tuples, as the render groups' pool holds.
COLUMNS_TUPLES = (
    (),
    ("a", "b"),
    ("a",),
    ("b", "a"),
    ("a", "b"),
    ("",),
    ("a", "b", "c", "d", "e"),
    (("a", "b"), ("c",)),
    ((), ("a",)),
    (("a", "b"), ("c",)),
)
# The mappings the generated mapping pools intern: the empty one, repeats, mappings that differ only in order, and ones a mapping pool refuses, which leave it as it was.
COLUMNS_MAPPINGS: tuple[dict, ...] = (
    {},
    {"ss01": "a", "ss02": "b"},
    {"ss02": "b", "ss01": "a"},
    {"ss01": "a"},
    {"ss01": "a", "ss02": "b"},
    {"": ""},
    {"ss01": 1},
    {1: "a"},
    {"ss03": None},
    {"ss01": "é"},
)
# The windows the generated window indexes hold: each a list of `(ordinal, window)` pairs as `WindowIndex.build` takes them, packed, with repeated windows, with ordinals out of order, empty, and with a window the word cannot hold in its order (longer than `WINDOW_SLOTS`, leading with zero, above U+FFFF, or empty), which turns the keys to tuples.
COLUMNS_WINDOWS: tuple[tuple[tuple[int, tuple[int, ...]], ...], ...] = (
    (),
    ((0, (0xE650,)),),
    (
        (0, (0xE650, 0xE670)),
        (1, (0xE650,)),
        (2, (0xE650, 0xE670)),
        (3, (0xE651,)),
        (4, (0xE650,)),
        (5, (0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF)),
        (6, (1, 2, 3, 4)),
        (7, (0xE650, 0xE670)),
    ),
    ((5, (2,)), (1, (1,)), (3, (2,)), (0, (1,)), (4, (2, 1))),
    ((0, (1, 2)), (1, (1, 2, 3, 4, 5)), (2, (1,)), (3, (1, 2, 3, 4, 5)), (4, (1, 2))),
    ((0, (0, 1)), (1, (1,)), (2, (0,)), (3, (0, 1))),
    ((0, (0x10000,)), (1, (0xFFFF,)), (2, (0x10000,)), (3, (1, 0x10000))),
    ((0, ()), (1, (1,)), (2, ()), (3, (0xE650, 0xE651))),
)
# The windows the generated window indexes are asked for besides their own: absent, longer than a word holds, leading with zero, above U+FFFF and empty.
COLUMNS_ASKED = ((7,), (0xE650, 0xE671), (1, 2, 3, 4, 5), (1, 2, 3, 4, 5, 6), (0, 1), (0,), (0x10000,), ())
# The values the generated `window_word` lines pack.
COLUMNS_WORDS = (
    (),
    (0,),
    (1,),
    (0, 1),
    (1, 0),
    (0xE650, 0xE670),
    (0xFFFF,) * 4,
    (1, 2, 3, 4),
    (1, 2, 3, 4, 5),
    (0x10000,),
    (1, 0x10000),
    (0xFFFF, 0x10000, 1),
)


def _key_column(keys: Sequence[bytes]) -> bytearray:
    return bytearray(b"".join(keys))


def _key_cases() -> Iterator[tuple[str, bytes, int, int, list[tuple]]]:
    """The generated key indexes: each its case's name, its column, width and count, and the calls on it: none, keys of each width, keys whose first eight bytes are one slot's and differ after, a key repeated, which is refused naming the first two ordinals that hold it, a width that is not a multiple of eight and a column too short for its count, which are refused, and finds of keys of other lengths, absent keys and every held key."""
    digests = [hashlib.sha256(f"key {index}".encode()).digest() for index in range(40)]
    shared = [b"\x05" * 8 + digest[8:] for digest in digests[:6]]
    cases: list[tuple[str, list[bytes], int, int]] = [
        ("empty", [], 32, 0),
        ("one", digests[:1], 32, 1),
        ("several", digests, 32, len(digests)),
        ("shared-slot", shared, 32, len(shared)),
        ("narrow", [digest[:8] for digest in digests[:9]], 8, 9),
        ("sixteen", [digest[:16] for digest in digests[:5]], 16, 5),
        ("repeated", [digests[0], digests[1], digests[2], digests[1], digests[0]], 32, 5),
        ("repeated-shared", [shared[0], shared[1], shared[1]], 32, 3),
        ("odd-width", digests[:3], 12, 3),
        ("short-column", digests[:3], 32, 4),
        ("longer-column", digests[:6], 32, 4),
        ("nothing", [], 0, 0),
    ]
    for name, keys, width, count in cases:
        column = _key_column(keys)
        held = [bytes(column[at : at + width]) for at in range(0, width * min(count, len(keys)), width or 1)]
        asked = [*held, digests[-1], digests[-1][:16], b"", b"\x05" * 8 + digests[-1][8:], *shared[:2]]
        calls = [("find", key) for key in asked] + [("nbytes",)]
        yield name, bytes(column), width, count, calls


def _columns_generated(_source: BuildSource) -> Iterator[Mapping[str, object]]:
    with _columns_following():
        yield from _columns_generated_lines()


def _columns_generated_lines() -> Iterator[Mapping[str, object]]:
    """The `columns` stage's generated lines: string tables, tuple pools and mapping pools over empty values, repeats and values the pools refuse (`COLUMNS_STRINGS`, `COLUMNS_TUPLES`, `COLUMNS_MAPPINGS`), each read back by id and measured; a tuple pool sealed; key indexes over empty, colliding, repeated and malformed columns (`_key_cases`); window indexes `WindowIndex.build` makes from `COLUMNS_WINDOWS`, each asked for its own windows and `COLUMNS_ASKED`, its runs and its size, and remapped with every other unit removed; `window_word` over `COLUMNS_WORDS`; and `sorted_by_word` over words empty, equal, sharing a top byte and at both ends of the range."""
    yield from _columns_traced(
        "generated:string-table:empty",
        "string-table",
        columns.StringTable,
        ("new",),
        [
            ("len",),
            ("getitem", 0),
            ("find", ""),
            ("find", "a"),
            ("optional", None),
            ("optional", ""),
            ("id", ""),
            ("len",),
        ],
    )
    strings: list[tuple] = []
    for text in COLUMNS_STRINGS:
        strings += [("find", text), ("id", text), ("optional", text), ("len",)]
    strings += [("getitem", index) for index in range(len(set(COLUMNS_STRINGS)))]
    yield from _columns_traced(
        "generated:string-table:strings", "string-table", columns.StringTable, ("new",), strings
    )
    tuples: list[tuple] = [("len",), ("getitem", 0)]
    for value in COLUMNS_TUPLES:
        tuples += [("id", value), ("pooled", value), ("len",)]
    distinct = len(set(COLUMNS_TUPLES))
    tuples += [("getitem", index) for index in range(distinct)]
    tuples += [
        ("seal",),
        ("id", ("a",)),
        ("pooled", ("a", "b")),
        ("id", ("z",)),
        ("getitem", 1),
        ("len",),
        ("seal",),
    ]
    yield from _columns_traced(
        "generated:tuple-pool:tuples", "tuple-pool", columns.TuplePool, ("new",), tuples
    )
    yield from _columns_traced(
        "generated:tuple-pool:sealed-empty",
        "tuple-pool",
        columns.TuplePool,
        ("new",),
        [("seal",), ("id", ()), ("len",)],
    )
    mappings: list[tuple] = [("len",), ("getitem", 0)]
    for mapping in COLUMNS_MAPPINGS:
        mappings += [("id", mapping), ("pooled", mapping), ("len",)]
    mappings += [("getitem", index) for index in range(6)]
    yield from _columns_traced(
        "generated:mapping-pool:mappings", "mapping-pool", columns.MappingPool, ("new",), mappings
    )
    for name, column, width, count, calls in _key_cases():
        yield from _columns_traced(
            f"generated:key-index:{name}",
            "key-index",
            functools.partial(columns.KeyIndex, bytearray(column), width, count),
            ("new", column[: width * count], width, count),
            calls,
        )
    for pairs in COLUMNS_WINDOWS:
        listed = [(ordinal, list(window)) for ordinal, window in pairs]
        index = columns.WindowIndex.build(pairs)
        yield _stateless_line(
            "generated", "window-index-build", listed, (index.ordinals.tolist(), list(index.keys))
        )
        size = 1 + max((ordinal for ordinal, _window in pairs), default=-1)
        removed = bytearray(position % 2 for position in range(size))
        survivor = [sum(1 for at in range(position) if not removed[at]) for position in range(size)]
        remapped = index.remapped(survivor, removed)
        for held in (index, remapped):
            state, line = _window_state("generated", held)
            if line is not None:
                yield line
            asked = {tuple(window) for _ordinal, window in pairs} | set(COLUMNS_ASKED)
            for window in sorted(asked, key=lambda window: (len(window), window)):
                yield _window_line(
                    "generated", state, ("candidates", list(window)), list(held.candidates(window))
                )
                yield _window_line("generated", state, ("key", list(window)), held.key(window))
            yield _window_line("generated", state, ("runs",), list(held.runs()))
            yield _window_line("generated", state, ("nbytes",), held.nbytes())
        yield _window_line(
            "generated",
            _window_state("generated", index)[0],
            ("remapped", survivor, bytes(removed)),
            _window_result("remapped", remapped),
        )
    for values in COLUMNS_WORDS:
        yield _stateless_line("generated", "window-word", list(values), columns.window_word(values))
    spread = [(index * 0x9E3779B97F4A7C15) % 2**64 for index in range(600)]
    for words in (
        [],
        [7],
        [3, 3, 3],
        [2**64 - 1, 0, 2**64 - 1, 0, 1],
        [0x0100000000000002, 0x0100000000000001, 0x01FFFFFFFFFFFFFF, 0x0100000000000001],
        spread,
        [word >> 60 << 56 | word & 0xFF for word in spread],
    ):
        yield _stateless_line(
            "generated", "sorted-by-word", words, columns.sorted_by_word(array("Q", words)).tolist()
        )


PERMUTATION_STAGE = "permutation"
_COUNTING_PERMUTATION = inspect.signature(audit._counting_permutation)
_TRIAGE_PERMUTATION = inspect.signature(audit._triage_permutation)
_TRIAGE_KEY = inspect.signature(audit.triage_key)
_FAMILY_RANKS = inspect.signature(audit.family_ranks)


def _permutation_line(origin: str, form: str, inputs: object, value: object) -> dict[str, object]:
    return {
        "key": f"{origin}:{form}:{_columns_digest(inputs)}",
        "form": form,
        "input": inputs,
        "value": value,
    }


def _table_strings(strings: columns.StringTable) -> list[str]:
    """A string table's strings in id order, the empty one first, read without a call the `columns` stage follows."""
    return list(strings._strings)


def _given_runs(runs: object) -> list[tuple[int, int]] | None:
    """The runs a `_counting_permutation` call was given, read without using them up: a sequence as its pairs, and a `columns.WindowIndex.runs` generator not yet started as the runs a probe over its index's columns gives (`_WINDOW_RUNS`)."""
    if runs is None:
        return None
    if isinstance(runs, list | tuple):
        return [(start, end) for start, end in runs]
    if (
        inspect.isgenerator(runs)
        and inspect.getgeneratorstate(runs) == inspect.GEN_CREATED
        and runs.gi_code is _WINDOW_RUNS.__code__
        and runs.gi_frame is not None
    ):
        index = runs.gi_frame.f_locals["self"]
        return list(_WINDOW_RUNS(columns.WindowIndex(index.ordinals, index.keys)))
    raise ValueError(f"the permutation stage cannot read the runs {runs!r:.80} without using them up")


def _counting_inputs(
    class_col: Sequence[int],
    group_col: Sequence[int],
    strings: columns.StringTable,
    class_order: Mapping[str, int],
    family_rank: Mapping[str, int],
    walk: object,
    n: int,
    runs: object,
    id_word: Callable[[int], int] | None,
) -> tuple:
    """A `_counting_permutation` call's inputs as a `counting` line holds them: the columns, the string table's strings, the orders as dicts, the walk as a list, the count, the runs as pairs or None (`_given_runs`), and each row's id word by ordinal or None. A walk the stage would use up by reading it is refused."""
    if not isinstance(walk, array | list | range | tuple):
        raise ValueError(f"the permutation stage cannot read the walk {walk!r:.80} without using it up")
    return (
        list(class_col),
        list(group_col),
        _table_strings(strings),
        dict(class_order),
        dict(family_rank),
        list(walk),
        n,
        _given_runs(runs),
        None if id_word is None else [id_word(ordinal) for ordinal in range(n)],
    )


def _triage_inputs(
    table: UnitTable,
    class_order: Mapping[str, int],
    family_rank: Mapping[str, int],
    id_word: Callable[[int], int] | None,
) -> tuple:
    """A `_triage_permutation` call's inputs as a `triage` line holds them: the table's class and group columns, its string table's strings, its window lengths, starts and code points, its count, the orders as dicts, and each row's id word by ordinal or None."""
    return (
        table._class.tolist(),
        table._group.tolist(),
        _table_strings(table.strings),
        table._window_n.tolist(),
        table._window_start.tolist(),
        table._window_values.tolist(),
        table.n,
        dict(class_order),
        dict(family_rank),
        None if id_word is None else [id_word(ordinal) for ordinal in range(table.n)],
    )


def _counted(args: tuple, kwargs: dict) -> Finisher:
    """An `audit._counting_permutation` call, with the permutation it gave."""
    bound = _COUNTING_PERMUTATION.bind(*args, **kwargs)
    bound.apply_defaults()
    inputs = _counting_inputs(**bound.arguments)
    return lambda result: _permutation_line("run", "counting", inputs, _columns_value(result))


def _triaged(args: tuple, kwargs: dict) -> Finisher:
    """An `audit._triage_permutation` call, with the permutation it gave."""
    bound = _TRIAGE_PERMUTATION.bind(*args, **kwargs)
    bound.apply_defaults()
    inputs = _triage_inputs(**bound.arguments)
    return lambda result: _permutation_line("run", "triage", inputs, _columns_value(result))


def _triage_keyed(args: tuple, kwargs: dict) -> Finisher:
    """An `audit.triage_key` call, with the key it gave."""
    bound = _TRIAGE_KEY.bind(*args, **kwargs)
    arguments = bound.arguments
    inputs = (
        arguments["class_index"],
        arguments["group"],
        tuple(arguments["codepoint_values"]),
        arguments["unit_id"],
        dict(arguments["family_rank"]),
    )
    return lambda result: _permutation_line("run", "triage-key", inputs, result)


def _family_ranked(args: tuple, kwargs: dict) -> Finisher:
    """An `audit.family_ranks` call, with the ranks it gave."""
    inputs = dict(_FAMILY_RANKS.bind(*args, **kwargs).arguments["family_of"])
    return lambda result: _permutation_line("run", "family-ranks", inputs, _columns_value(result))


# The windows the generated tied table's units take in turn, two to four letters long, one leading with a space and two sharing their letters in another order.
PERMUTATION_TIED_WINDOWS = (
    "E650:E652",
    "E652:E650",
    "E650:E651:E652",
    "0020:E650:E652",
    "E653:E650",
    "E650:E652:E650:E651",
)


def _permutation_tables(source: BuildSource) -> Iterator[tuple[str, UnitTable, list[str]]]:
    """The tables the generated permutations order, each named, with its ledger's class ids in order: the source's audit as the in-memory source loads it (`audit.load_audit_table`), and a table whose units tie on class, group and window by the dozen, across two classes, three groups and windows of two to four letters, in an order that is neither load nor window order (`PERMUTATION_TIED_WINDOWS`)."""
    inputs = source.case.inputs
    ledger = load_ledger(inputs.ledger)
    parts = audit.AuditParts(inputs.audit)
    try:
        table, _rows = audit.load_audit_table(
            inputs.audit, ledger, dict(LETTERS), descriptors=parts.descriptors, members=parts.members
        )
    finally:
        parts.close()
    yield "source", table, [entry.id for entry in ledger]
    rows = [
        audit.AuditRow(
            "default",
            PERMUTATION_TIED_WINDOWS[index * 7 % len(PERMUTATION_TIED_WINDOWS)],
            ("cell",),
            ("class-a", "class-b")[index % 2],
            ("a",),
            (f"n{index}",),
        )
        for index in range(60)
    ]
    tied_ledger = [
        audit.LedgerClass(class_id, "by-design", "", False, False, frozenset())
        for class_id in ("class-b", "class-a")
    ]
    tied, _rows = audit.load_table(rows, tied_ledger, dict(LETTERS))
    yield "tied", tied, ["class-b", "class-a"]


def _permutation_words(table: UnitTable) -> Iterator[list[int] | None]:
    """The id words the generated permutations break ties by, each row's by ordinal: none; words spread across 64 bits but every fifth row's equal; and words by the row's window, siblings differing in their last bit only on odd ordinals."""
    yield None
    yield [(ordinal * 0x9E3779B97F4A7C15) % 2**64 if ordinal % 5 else 7 << 56 for ordinal in range(table.n)]
    yield [
        int(hashlib.sha256(table.codepoints_text(ordinal).encode()).hexdigest()[:16], 16) ^ ordinal % 2
        for ordinal in range(table.n)
    ]


def _permutation_sorts(
    origin: str,
    class_col: Sequence[int],
    group_col: Sequence[int],
    strings: columns.StringTable,
    class_order: Mapping[str, int],
    family_rank: Mapping[str, int],
    walks: Sequence[Sequence[int]],
    n: int,
    runs: list[tuple[int, int]],
    words: list[int] | None,
) -> Iterator[Mapping[str, object]]:
    """A `counting` line for each walk, with the runs and without, each valued what `audit._counting_permutation` gives."""
    id_word = None if words is None else words.__getitem__
    for walk in walks:
        for given in (runs, None):
            inputs = _counting_inputs(
                class_col, group_col, strings, class_order, family_rank, walk, n, given, id_word
            )
            value = audit._counting_permutation(
                class_col, group_col, strings, class_order, family_rank, walk, n, runs=given, id_word=id_word
            )
            yield _permutation_line(origin, "counting", inputs, value.tolist())


def _permutation_edges() -> Iterator[Mapping[str, object]]:
    """The generated lines at the edges: family ranks over the alphabet, nothing, and a family named twice; triage keys over empty, unknown and repeated groups, empty and astral windows and ids of every width; and counting sorts over no rows, and over rows whose classes the order leaves out, whose groups name unknown families or share a rank tuple under different names, walked short of their count or with every id word equal."""
    for family_of in (dict(LETTERS), {}, {1: "a", 2: "b", 3: "a", 4: ""}):
        yield _permutation_line("generated", "family-ranks", family_of, audit.family_ranks(family_of))
    rank = audit.family_ranks(dict(LETTERS))
    for class_index, group, codepoints, unit_id in (
        (0, "", (), ""),
        (7, "qsPea", (0xE650,), "u-a"),
        (1, "qsNope:qsPea:qsTea", (0x20, 0xE650, 0xE652), "é·Day"),
        (2, ":", (0x10FFFF, 0), "u-\U0001f600"),
        (0, "qsPea:qsPea", (0xE650, 0xE650), "u-11111111111"),
    ):
        inputs = (class_index, group, codepoints, unit_id, rank)
        yield _permutation_line("generated", "triage-key", inputs, audit.triage_key(*inputs))
    yield from _permutation_sorts("generated", [], [], columns.StringTable(), {}, rank, [[]], 0, [], [7])
    strings = columns.StringTable()
    names = ("class-a", "class-b", "class-x", "qsPea:qsTea", "qsNope:qsPea", "qsGone:qsPea", "", "qsTea")
    ids = [strings.id(name) for name in names]
    class_col = [ids[index % 3] for index in range(12)]
    group_col = [ids[3 + index % 5] for index in range(12)]
    walks = [[11, 3, 5, 0, 1, 2, 4, 6, 7, 8, 9, 10], [4, 2, 0], list(range(12))]
    for class_order in ({"class-a": 0, "class-b": 1, "class-x": 2}, {"class-b": 0}, {}):
        for words in (None, [5] * 12, [(index * 0x9E3779B97F4A7C15) % 2**64 for index in range(12)]):
            yield from _permutation_sorts(
                "generated",
                class_col,
                group_col,
                strings,
                class_order,
                rank,
                walks,
                12,
                [(0, 3), (5, 9)],
                words,
            )


def _permutation_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `permutation` stage's generated lines: each table of `_permutation_tables` ordered under its ledger's class order and a reversed one that leaves its first class unranked, with each of `_permutation_words`, by `audit._triage_permutation` as a `triage` line and by `audit._counting_permutation` walked in window order and in load order, with the window index's runs and without (`_permutation_sorts`); every unit of each keyed by `audit.triage_key` under the ledger's order; and `_permutation_edges`."""
    rank = audit.family_ranks(dict(LETTERS))
    for name, table, classes in _permutation_tables(source):
        index = table.window_index()
        runs = list(index.runs())
        walks = [index.ordinals, range(table.n)]
        for ordered in (classes, classes[:0:-1]):
            class_order = {class_id: position for position, class_id in enumerate(ordered)}
            for words in _permutation_words(table):
                id_word = None if words is None else words.__getitem__
                yield _permutation_line(
                    f"generated:{name}",
                    "triage",
                    _triage_inputs(table, class_order, rank, id_word),
                    audit._triage_permutation(table, class_order, rank, id_word).tolist(),
                )
                yield from _permutation_sorts(
                    f"generated:{name}",
                    table._class,
                    table._group,
                    table.strings,
                    class_order,
                    rank,
                    walks,
                    table.n,
                    runs,
                    words,
                )
        class_index = {class_id: position for position, class_id in enumerate(classes)}
        for ordinal in range(table.n):
            inputs = (
                class_index.get(table.class_id(ordinal), len(class_index)),
                table.group(ordinal),
                table.codepoints(ordinal),
                f"u-{name}-{ordinal}",
                rank,
            )
            yield _permutation_line(f"generated:{name}", "triage-key", inputs, audit.triage_key(*inputs))
    yield from _permutation_edges()


MERGE_STAGE = "merge"
_MERGE_MEMBERS = inspect.signature(audit._merge_members)
# The counts a merge's stats dict holds, in its order, as `audit.stream_workload` and `audit.merge_ink_duplicate_units` start it.
MERGE_STATS = ("windows_merged", "units_merged", "kept_split_matched_classes")


def _member_value(member: audit._Member) -> Record:
    """A merge member as a `merge` line holds it: `audit._Member`'s fields in their order, its config classes as a dict in their order."""
    return Record(
        "_Member",
        tuple(
            (item.name, dict(value.items()) if item.name == "config_classes" else value)
            for item in dataclasses.fields(member)
            for value in (getattr(member, item.name),)
        ),
    )


def _member_runs(absorb: Callable, members: Sequence[audit._Member]) -> list[list[tuple[int, str]]]:
    """Each member's run of rows as `absorb` merges it, aligned with `members`, a row as `(merge key, config)`: read from the closure of the stream's `absorb` (`units` and `section_configs`), keyed by section, or of the in-memory source's (`table` and `rows`), keyed by config rank."""
    closure = inspect.getclosurevars(absorb).nonlocals
    if "units" in closure and "section_configs" in closure:
        units, section_configs = closure["units"], closure["section_configs"]
        return [[(row[0], section_configs[row[0]]) for row in units[member.ordinal]] for member in members]
    if "table" in closure and "rows" in closure:
        table, rows = closure["table"], closure["rows"]
        runs = []
        for member in members:
            start = table.rows_start(member.ordinal)
            runs.append(
                [
                    (rows.ranks[rows.config[index]], rows.config_at(index))
                    for index in range(start, start + table.row_count(member.ordinal))
                ]
            )
        return runs
    raise ValueError(f"the merge stage cannot read the runs {absorb!r:.80} merges")


def _merge_line(origin: str, inputs: object, value: object) -> dict[str, object]:
    return {"key": f"{origin}:{_columns_digest(inputs)}", "input": inputs, "value": value}


def _merge_recorder(
    origin: str,
    members: list[audit._Member],
    exempt_classes: Collection[str],
    stats: dict,
    absorb: Callable,
) -> Callable[[object], dict[str, object]]:
    """A `_merge_members` call's line, keyed by its origin and a digest of its input: the members in the order given, the exempt classes sorted, the stats and each member's run (`_member_runs`); valued whether any member was absorbed, the members in the order the call leaves them, the stats and each member's run, aligned with the members as given, once the call returns, or a raised `("raised", class, message)`."""
    held = list(members)
    inputs = (
        [_member_value(member) for member in held],
        sorted(exempt_classes),
        dict(stats),
        _member_runs(absorb, held),
    )

    def finish(result: object) -> dict[str, object]:
        if isinstance(result, Exception):
            return _merge_line(origin, inputs, ("raised", type(result).__name__, str(result)))
        value = (
            result,
            [_member_value(member) for member in members],
            dict(stats),
            _member_runs(absorb, held),
        )
        return _merge_line(origin, inputs, value)

    return finish


def _members_merged(args: tuple, kwargs: dict) -> Finisher:
    """An `audit._merge_members` call, with what it left."""
    arguments = _MERGE_MEMBERS.bind(*args, **kwargs).arguments
    return _merge_recorder(
        "run", arguments["members"], arguments["exempt_classes"], arguments["stats"], arguments["absorb"]
    )


@contextlib.contextmanager
def _merges_into(origin: str, lines: list[dict[str, object]]) -> Iterator[None]:
    """Record every `audit._merge_members` call made inside as a `merge` line of `origin` into `lines`."""
    original = audit._merge_members

    def call(members, exempt_classes, stats, absorb):
        finish = _merge_recorder(origin, members, exempt_classes, stats, absorb)
        result = original(members, exempt_classes, stats, absorb)
        lines.append(finish(result))
        return result

    setattr(audit, "_merge_members", call)
    try:
        yield
    finally:
        setattr(audit, "_merge_members", original)


def _section_absorb(units: list[list[tuple[int, tuple, str]]], section_configs: Sequence[str]) -> Callable:
    """An `absorb` that merges two members' runs as the stream's does (`audit._merged_rows`), each run a list of `(section, fields, line)` rows, and returns the merged run's configs."""

    def absorb(survivor: audit._Member, other: audit._Member) -> tuple[str, ...]:
        merged: list[tuple[int, tuple, str]] = audit._merged_rows(
            units[survivor.ordinal], units[other.ordinal]
        )
        units[survivor.ordinal] = merged
        return tuple(section_configs[section] for section, _fields, _line in merged)

    return absorb


# The ink digests the generated stream merges take a window's units under: one signature for every row, so every window of siblings is one group; the row's configuration, so only units of one configuration merge; and the row's signature key modulo three.
MERGE_DIGESTS: dict[str, Callable[[int, str, str], object]] = {
    "one": lambda _key, _window, _config: 0,
    "config": lambda _key, _window, config: config,
    "key": lambda key, _window, _config: key % 3,
}
# The ink signatures the generated in-memory merges take a window under: one for every configuration, and the configuration itself.
MERGE_SIGNATURES: dict[str, Callable[[str, str], object]] = {
    "one": lambda _codepoints, _config: 0,
    "config": lambda _codepoints, config: config,
}
# The sections the generated edge cases' runs name, a row's first field indexing them.
MERGE_SECTIONS = ("default", "ss01", "ss02", "ss03", "ss04", "ss05")


def _merge_member(
    ordinal: int,
    rank: int,
    config_classes: Mapping[str, str],
    kinds: tuple[str, ...] = ("diff",),
    exemplar: bool = False,
    exempt: Collection[str] = (),
) -> audit._Member:
    """A generated member as the stream makes one: its configs those its classes name, its class resolved by the unmatched-first rule, or "" with no classes."""
    classes = set(config_classes.values())
    matched = sorted(classes - {audit.UNMATCHED_CLASS})
    class_id = audit.UNMATCHED_CLASS if audit.UNMATCHED_CLASS in classes else (matched or [""])[0]
    return audit._Member(
        ordinal,
        rank,
        tuple(config_classes),
        kinds,
        dict(config_classes),
        exemplar,
        class_id,
        class_id in exempt,
    )


def _merge_edges() -> Iterator[tuple[str, list[audit._Member], frozenset[str], dict, list[list[int]]]]:
    """The generated windows at the edges, each named, with its members, exempt classes, starting stats and each member's run of sections by ordinal: members out of rank order and tied on rank, kinds that sort past ASCII, exemplars; two matched classes kept split, a chain that splits once the survivor has widened, and a survivor whose own classes are two; an unmatched member beside a matched one, a merge whose later entry overrides the survivor's only matched one, exempt classes, a single member, members with no classes at all, and no members."""
    unmatched = audit.UNMATCHED_CLASS
    zero = dict.fromkeys(MERGE_STATS, 0)
    counted = {"windows_merged": 3, "units_merged": 5, "kept_split_matched_classes": 1}
    yield (
        "ordered",
        [
            _merge_member(0, 4, {"ss04": unmatched}, ("é", "diff")),
            _merge_member(1, 1, {"ss01": unmatched, "ss03": unmatched}, ("z", "diff"), exemplar=True),
            _merge_member(2, 2, {"ss02": unmatched}, ("a",)),
            _merge_member(3, 1, {"ss05": unmatched}, ("\U0001f600", "a")),
        ],
        frozenset(),
        counted,
        [[4], [1, 3], [2], [5]],
    )
    yield (
        "split",
        [_merge_member(0, 0, {"default": "class-a"}), _merge_member(1, 1, {"ss01": "class-b"})],
        frozenset(),
        zero,
        [[0], [1]],
    )
    yield (
        "chain",
        [
            _merge_member(0, 0, {"default": "class-a"}),
            _merge_member(1, 1, {"ss01": unmatched}),
            _merge_member(2, 2, {"ss02": "class-b"}),
            _merge_member(3, 3, {"ss03": "class-a"}, exemplar=True),
        ],
        frozenset({"class-a"}),
        counted,
        [[0, 4], [1, 1], [2], [3, 5]],
    )
    yield (
        "two-classes",
        [
            _merge_member(1, 0, {"default": "class-a", "ss01": "class-b"}),
            _merge_member(0, 2, {"ss02": unmatched}),
        ],
        frozenset(),
        zero,
        [[0, 1], [2]],
    )
    yield (
        "unmatched-beside-matched",
        [
            _merge_member(0, 0, {"default": "class-a"}, exempt={"class-a"}),
            _merge_member(1, 1, {"ss01": unmatched}),
        ],
        frozenset({"class-a"}),
        zero,
        [[0], [1]],
    )
    yield (
        "override",
        [
            _merge_member(0, 0, {"default": "class-a"}),
            _merge_member(1, 0, {"default": unmatched, "ss01": "class-a"}),
            _merge_member(2, 0, {"default": unmatched}),
        ],
        frozenset({"class-a", unmatched}),
        zero,
        [[0, 2], [0, 1], [0]],
    )
    yield (
        "exempt",
        [
            _merge_member(2, 3, {"ss03": "class-c"}, exempt={"class-c"}),
            _merge_member(0, 3, {"ss04": "class-c"}, exempt={"class-c"}),
            _merge_member(1, 0, {"default": "class-c"}, exempt={"class-c"}),
        ],
        frozenset({"class-c", "class-d"}),
        counted,
        [[3], [4], [0]],
    )
    yield ("single", [_merge_member(0, 0, {"default": unmatched})], frozenset(), zero, [[0]])
    yield ("empty", [], frozenset(), zero, [])
    yield (
        "no-classes",
        [_merge_member(0, 0, {}), _merge_member(1, 1, {})],
        frozenset(),
        zero,
        [[], []],
    )


def _merge_sources(source: BuildSource) -> Iterator[tuple[str, Callable[[], object]]]:
    """The merges the generated lines record over the source's audit, each named, as a call that runs it: the stream over the held audit under each of `MERGE_DIGESTS`, when the audit is in sorted sections, and the in-memory source's merge over a fresh load of its table under each of `MERGE_SIGNATURES`."""
    inputs = source.case.inputs
    ledger = load_ledger(inputs.ledger)
    exempt = frozenset(entry.id for entry in ledger if entry.no_verdict)
    document = json.loads((source.out / BUILD_INPUT_FILE).read_text(encoding="ascii"))
    family_of = {int(value): name for value, name in document["family_of"]}
    family_keys = document["family_keys"]
    parts = audit.AuditParts(inputs.audit)
    try:
        layout = audit.audit_layout(parts.descriptors, inputs.audit, parts.members)
        if layout.sections is not None:
            for name, digest in MERGE_DIGESTS.items():
                yield (
                    f"stream:{name}",
                    functools.partial(
                        audit.stream_workload,
                        parts.descriptors,
                        inputs.audit,
                        inputs.ledger,
                        layout.sections,
                        ledger,
                        family_of,
                        family_keys,
                        digest,
                    ),
                )
        for name, ink_sig in MERGE_SIGNATURES.items():
            table, rows = audit.load_audit_table(
                inputs.audit, ledger, dict(LETTERS), descriptors=parts.descriptors, members=parts.members
            )
            yield (
                f"table:{name}",
                functools.partial(audit.merge_ink_duplicate_units, table, rows, ink_sig, exempt),
            )
    finally:
        parts.close()


def _merge_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `merge` stage's generated lines: every `audit._merge_members` call the merges of `_merge_sources` make, recorded as the build's are (`_merges_into`), and each window of `_merge_edges`, merged over its runs by the stream's `absorb` (`_section_absorb`)."""
    for name, merge in _merge_sources(source):
        lines: list[dict[str, object]] = []
        with _merges_into(f"generated:{name}", lines):
            merge()
        yield from lines
    for name, members, exempt, starting, sections in _merge_edges():
        stats = dict(starting)
        units = [[(section, (), "") for section in run] for run in sections]
        absorb = _section_absorb(units, MERGE_SECTIONS)
        finish = _merge_recorder(f"generated:edge:{name}", members, exempt, stats, absorb)
        try:
            result: object = audit._merge_members(members, exempt, stats, absorb)
        except (IndexError, KeyError) as error:
            result = error
        yield finish(result)


WORKLOAD_STAGE = "workload"
# The keys of the `workload` stage's lines beside its units and columns: the pools' sizes, the window index's shape, the merge's stats, the audit's rows, the ledger classes the units reach, the classes present and the change groups' counts.
WORKLOAD_POOLS_KEY = "pools"
WORKLOAD_INDEX_KEY = "window-index"
WORKLOAD_MERGE_KEY = "merge"
WORKLOAD_ROWS_KEY = "rows"
WORKLOAD_REACHED_KEY = "classes-reached"
WORKLOAD_PRESENT_KEY = "classes-present"
WORKLOAD_GROUPS_KEY = "unmatched-groups"


def _workload_ids(table: UnitTable, ordinal: int) -> tuple:
    """A unit's row of the table's id columns, in `UnitTable.columns`' order, the name ids None once released."""
    baseline, new = table._baseline, table._new
    return (
        table._class[ordinal],
        table._group[ordinal],
        table._unmatched_group[ordinal],
        table._duplicate_group[ordinal],
        table._cluster[ordinal],
        table._configs[ordinal],
        table._kinds[ordinal],
        table._render_groups[ordinal],
        None if baseline is None else baseline[ordinal],
        None if new is None else new[ordinal],
        table._config_classes[ordinal],
        table._flags[ordinal],
        table._window_start[ordinal],
        table._window_n[ordinal],
        table._rows_start[ordinal],
        table._row_count[ordinal],
        table._order[ordinal],
        table._batch[ordinal],
        table._survivor[ordinal],
    )


def _workload_unit(table: UnitTable, keys: bytearray, ordinal: int) -> tuple:
    """A unit as the `workload` boundary holds it: the unit `UnitTable.unit` materializes without a store, as a record of `audit.Unit`'s fields in their order but `ink_deltas`, its config classes as a dict in their order; whether it is live, its survivor and its config rank; its row of the id columns (`_workload_ids`); and its input key as hex."""
    return (
        _unit_value(table.unit(ordinal)),
        table.live(ordinal),
        table.survivor(ordinal),
        table.config_rank(ordinal),
        _workload_ids(table, ordinal),
        bytes(keys[ordinal * audit.INPUT_KEY_BYTES : (ordinal + 1) * audit.INPUT_KEY_BYTES]).hex(),
    )


def _pool_values[T](pool: columns.TuplePool[T] | columns.MappingPool | None) -> list:
    """A pool's values in id order, the empty one at id 0 first, mappings as dicts; nothing for a released pool."""
    if pool is None:
        return []
    return [
        dict(value.items()) if isinstance(value, Mapping) else value
        for value in (pool[index] for index in range(len(pool) + 1))
    ]


def workload_boundary(streamed: audit.StreamedLoad) -> Boundary:
    """The `workload` boundary over what `audit.stream_workload` returned: a unit a merged unit (`_workload_unit`); a column a pool and table side column in id or position order, the string table, the config and kind tuples, the render groups, the class maps, the name tuples, the tuple ranks and the window values; the window index's ordinals and keys, empty when the stream left it unbuilt; the pre-merge units' columns and the compaction, a pre-merge unit each; each class's ordinals in row order (`UnitTable.rows_by_class`); and the pools' sizes, the window index's packing and window count or None, the merge's stats, the audit's rows, the ledger classes the units reach, the classes present sorted and the change groups' counts."""
    workload = streamed.workload
    table = workload.table
    keys = streamed.input_keys
    index = table._window_index
    strings, names = table.strings, table.names
    premerge, compaction = streamed.premerge, streamed.compaction
    pools = (
        len(strings),
        strings.chars,
        len(table.tuples),
        table.tuples.elements,
        len(table.groups),
        table.groups.elements,
        len(table.mappings),
        table.mappings.elements,
        None if names is None else (len(names), names.elements, names.sealed),
    )
    return Boundary(
        table.n,
        {"value": functools.partial(_workload_unit, table, keys)},
        [
            column_line("strings", (strings[at] for at in range(len(strings) + 1))),
            column_line("tuples", _pool_values(table.tuples)),
            column_line("groups", _pool_values(table.groups)),
            column_line("mappings", _pool_values(table.mappings)),
            column_line("names", _pool_values(names)),
            column_line("ranks", table._ranks),
            column_line("window-values", table._window_values),
            column_line("window-ordinals", () if index is None else index.ordinals),
            column_line("window-keys", () if index is None else index.keys),
            column_line(
                "premerge",
                zip(
                    premerge.class_id,
                    (bool(value) for value in premerge.no_verdict),
                    premerge.configs,
                    premerge.window_start,
                    premerge.window_n,
                ),
            ),
            column_line(
                "compaction", zip(compaction.survivor, (bool(value) for value in compaction.removed))
            ),
            column_line(
                "rows-by-class",
                (
                    (class_id, tuple(members))
                    for class_id, members in table.rows_by_class(range(table.n)).items()
                ),
            ),
            {"key": WORKLOAD_POOLS_KEY, "value": pools},
            {"key": WORKLOAD_INDEX_KEY, "value": None if index is None else (index.packed, index.windows)},
            {"key": WORKLOAD_MERGE_KEY, "value": dict(streamed.merge_stats)},
            {"key": WORKLOAD_ROWS_KEY, "value": workload.row_count},
            {"key": WORKLOAD_REACHED_KEY, "value": tuple(entry.id for entry in workload.classes_present)},
            {"key": WORKLOAD_PRESENT_KEY, "value": sorted(table.classes_present())},
            {"key": WORKLOAD_GROUPS_KEY, "value": table.unmatched_group_counts()},
        ],
    )


def _workload_streamed(_args: tuple, _kwargs: dict) -> Finisher:
    """The build's stream (`audit.stream_workload`) recorded as the `workload` boundary once it returns (`workload_boundary`)."""

    def finish(result: object) -> Boundary:
        assert isinstance(result, audit.StreamedLoad), f"stream_workload returned {result!r:.80}"
        return workload_boundary(result)

    return finish


PREMERGE_STAGE = "premerge"
# The keys of the `premerge` stage's lines beside its units and columns: the identical classes and each column's length, in `PremergeSnapshot.columns`' order.
PREMERGE_IDENTICAL_KEY = "identical-classes"
PREMERGE_LENGTHS_KEY = "column-lengths"


def premerge_boundary(snapshot: facts.PremergeSnapshot) -> Boundary:
    """The `premerge` boundary over a rebased snapshot: a unit a pre-merge unit in capture order, its row of the snapshot's columns in `PremergeSnapshot.columns`' order (class id, no-verdict byte, config-set id, window start, window count, survivor, removed byte) followed by its grain as `PremergeSnapshot.grains` yields it, as a tuple; the identical classes sorted, and each column's length."""
    survivor, removed = snapshot.survivor, snapshot.removed
    assert survivor is not None and removed is not None, "the premerge stage records a rebased snapshot"
    strings, tuples = snapshot.strings, snapshot.tuples

    def row(at: int) -> tuple:
        return (
            snapshot.class_id[at],
            snapshot.no_verdict[at],
            snapshot.configs[at],
            snapshot.window_start[at],
            snapshot.window_n[at],
            survivor[at],
            removed[at],
            (
                audit.format_codepoints(snapshot.codepoints(at)),
                strings[snapshot.class_id[at]],
                bool(snapshot.no_verdict[at]),
                tuples[snapshot.configs[at]],
            ),
        )

    return Boundary(
        len(snapshot),
        {"value": row},
        [
            {"key": PREMERGE_IDENTICAL_KEY, "value": sorted(snapshot.identical_classes)},
            {"key": PREMERGE_LENGTHS_KEY, "value": [len(column) for column in snapshot.columns()]},
        ],
    )


def _premerge_rebased(args: tuple, _kwargs: dict) -> Finisher:
    """The build's pre-merge snapshot (`facts.PremergeSnapshot.rebase`) recorded as the `premerge` boundary once the rebase returns (`premerge_boundary`)."""
    snapshot = args[0]
    assert isinstance(snapshot, facts.PremergeSnapshot), f"rebase was called on {snapshot!r:.80}"
    return lambda _result: premerge_boundary(snapshot)


LOAD_STAGE = "load"
# The keys of the `load` stage's lines beside its units and the pre-merge column: the source the load took, the key-only read, the signature store's state, the stamps, the signature stamp's value, the merge's stats, the held audit's facts and the console lines the load printed.
LOAD_SOURCE_KEY = "source"
LOAD_KEY_ONLY_KEY = "key-only"
LOAD_SIGNATURES_KEY = "signatures"
LOAD_STAMPS_KEY = "stamps"
LOAD_ENVIRONMENT_KEY = "environment"
LOAD_MERGE_KEY = "merge"
LOAD_AUDIT_KEY = "audit"
LOAD_CONSOLE_KEY = "console"
# The key-only read's part of the load's note when the read ran, whose seconds and resident peak vary from run to run.
_KEY_ONLY_RAN = re.compile(r"ran [0-9.]+ s across ([0-9]+) processes, ")


def _key_only_fact(note: str) -> tuple:
    """The key-only read's part of the load's note (`build._key_only_note`) as its facts: `("ran", processes)`, `("skipped",)` when the store's stamps held, or `("not run", note)`."""
    if ran := _KEY_ONLY_RAN.match(note):
        return ("ran", int(ran.group(1)))
    if note.startswith("skipped, "):
        return ("skipped",)
    return ("not run", note)


def load_boundary(loaded: build._Loaded, printed: Sequence[str]) -> Boundary:
    """The `load` boundary over what the build's load gives the plan (`build._Loaded`): a unit a merged unit in load order as the `workload` boundary holds it (`_workload_unit`); a column over the pre-merge snapshot's rows as the `premerge` boundary holds them, without their grains; the source, the key-only read's facts (`_key_only_fact`), the signature store's hits, misses shaped, unhit entries, misses, whether it is unchanged and its new lines (`_store_lines`), the stamps, the signature stamp's value, the merge's stats, the held audit's parts, compressed parts and segment rows, and the console lines `printed` while the load ran."""
    table = loaded.workload.table
    snapshot = loaded.premerge
    survivor, removed = snapshot.survivor, snapshot.removed
    assert survivor is not None and removed is not None, "the load rebases its pre-merge snapshot"
    signatures = loaded.signatures
    return Boundary(
        table.n,
        {"value": functools.partial(_workload_unit, table, loaded.input_keys)},
        [
            column_line(
                "premerge",
                (
                    (
                        snapshot.class_id[at],
                        snapshot.no_verdict[at],
                        snapshot.configs[at],
                        snapshot.window_start[at],
                        snapshot.window_n[at],
                        survivor[at],
                        removed[at],
                    )
                    for at in range(len(snapshot))
                ),
            ),
            {"key": LOAD_SOURCE_KEY, "value": loaded.source},
            {"key": LOAD_KEY_ONLY_KEY, "value": _key_only_fact(loaded.key_only)},
            {
                "key": LOAD_SIGNATURES_KEY,
                "value": (
                    signatures.hits(),
                    loaded.shaped,
                    signatures.unhit(),
                    signatures.misses(),
                    signatures.unchanged(),
                    _store_lines(signatures),
                ),
            },
            {"key": LOAD_STAMPS_KEY, "value": loaded.stamps},
            {"key": LOAD_ENVIRONMENT_KEY, "value": loaded.environment.value},
            {"key": LOAD_MERGE_KEY, "value": loaded.merge_stats},
            {"key": LOAD_AUDIT_KEY, "value": (loaded.parts, loaded.compressed, loaded.segment_rows)},
            {"key": LOAD_CONSOLE_KEY, "value": list(printed)},
        ],
    )


class _Printed(io.TextIOBase):
    """Standard error while the load runs: each write goes on to `through`, and the text is kept to be split into the lines the load printed."""

    def __init__(self, through: IO[str]) -> None:
        self.through = through
        self.text: list[str] = []

    def write(self, text: str) -> int:
        self.text.append(text)
        return self.through.write(text)

    def flush(self) -> None:
        self.through.flush()

    def lines(self) -> list[str]:
        return "".join(self.text).splitlines()


def _corpus_loaded(_args: tuple, _kwargs: dict) -> Finisher:
    """The build's load (`build._load_corpus`) recorded as the `load` boundary once it returns (`load_boundary`), with the console lines it printed, read from standard error, which is held for the length of the call."""
    printed = _Printed(sys.stderr)
    sys.stderr = printed

    def finish(loaded: object) -> Boundary:
        sys.stderr = printed.through
        assert isinstance(loaded, build._Loaded), f"_load_corpus returned {loaded!r:.80}"
        return load_boundary(loaded, printed.lines())

    return finish


STORE_RECORDS_STAGE = "store-records"
# The store-record functions the `store-records` recorders and generated lines run themselves, taken before any build wraps them.
_PARSED_CACHED_UNIT = unit_cache._parsed_cached_unit
_RECORD_LINE = unit_cache.record_line
_PRIOR_RECORD_OR_NONE = unit_cache.prior_record_or_none
_NAMED_ORDINAL = unit_cache.named_ordinal
_ID_WORD = unit_cache.id_word
_PRIMARY_UNITS_CRC = unit_cache.primary_units_crc
_IS_CONTENT_ID = unit_cache.is_content_id
_PARSED_CACHED_UNIT_SIGNATURE = inspect.signature(unit_cache._parsed_cached_unit)
_RECORD_LINE_SIGNATURE = inspect.signature(unit_cache.record_line)
_PRIOR_RECORD_SIGNATURE = inspect.signature(unit_cache.prior_record_or_none)
_NAMED_ORDINAL_SIGNATURE = inspect.signature(unit_cache.named_ordinal)
# What a parse raises that fails the build rather than the store (`unit_cache._STORE_ERRORS` holds what the plan takes as a store that stops reading).
_RECORD_FAILURES = (IndexError, AttributeError)
# The shard part the generated record's address names, which its generated parses trust or not.
RECORDS_PART = "units/a-class.x000.json"


def _records_line(origin: str, form: str, inputs: object, value: object) -> dict[str, object]:
    """One `store-records` line, keyed by its origin, its form and a digest of its input, with what Python gave as its `value`."""
    return {
        "key": f"{origin}:{form}:{_columns_digest(inputs)}",
        "form": form,
        "input": inputs,
        "value": value,
    }


def _held_int(value: object) -> bool:
    return type(value) is int and -(2**63) <= value < 2**63


def _held_pair(value: object) -> bool:
    return type(value) is tuple and len(value) == 2 and all(_held_int(item) for item in value)


def _held_texts(values: Iterable[object]) -> bool:
    return all(type(value) is str for value in values)


def _held_optional_text(value: object) -> bool:
    return value is None or type(value) is str


def _held_address(address: object) -> bool:
    return address is None or (
        type(address) is tuple and type(address[0]) is str and _held_int(address[1]) and _held_int(address[2])
    )


def _parsed_held(parsed: unit_cache.ParsedCachedUnit) -> bool:
    """Whether every field of a parsed record but `junction_rects`, which the Rust record keeps as the line holds it, has the type `CachedUnit.to_record` writes it in, which the Rust record holds."""
    return (
        _held_texts(
            (
                parsed.key,
                parsed.prior_id,
                parsed.prior_class,
                parsed.content_key,
                parsed.diffs_digest,
                parsed.cluster,
                parsed.change_group,
                parsed.ledger_class,
                *parsed.ink_deltas,
                *parsed.ink_deltas.values(),
                *parsed.mismatches,
                *parsed.after_cells,
                *parsed.after_junctions,
                *parsed.before_glyphs,
                *parsed.before_junctions,
            )
        )
        and all(
            type(flag) is bool
            for flag in (
                parsed.slim,
                parsed.ink_identical,
                parsed.picture_identical,
                parsed.junior_equivalent,
                parsed.exemplar,
                parsed.no_verdict,
            )
        )
        and _held_address(parsed.address)
        and all(
            pair is None or _held_pair(pair)
            for pair in (
                parsed.pair_codepoints,
                parsed.pair,
                *parsed.after_spans,
                *parsed.before_spans,
                *parsed.junction_pairs,
            )
        )
        and _held_optional_text(parsed.duplicate_group)
        and _held_optional_text(parsed.policy_file)
        and all(_held_optional_text(unit) for unit, _suppressed in parsed.primary_units)
        and _held_int(parsed.inflated)
    )


def _parsed_value(parsed: object) -> tuple:
    assert isinstance(parsed, unit_cache.ParsedCachedUnit), f"_parsed_cached_unit returned {parsed!r:.80}"
    return ("parsed", parsed) if _parsed_held(parsed) else ("unheld",)


def _parsed_outcome(record: object, trusted: frozenset[str]) -> tuple:
    """What `_parsed_cached_unit` gives for a record: `("parsed", record)`, `("unheld",)` for a record the Rust side does not hold (`_parsed_held`), or `("raised", kind)`, `unreadable` for what `unit_cache.StoreUnreadable` wraps and `failed` for what fails the build."""
    try:
        parsed = _PARSED_CACHED_UNIT(record, trusted, {}, False)  # pyright: ignore[reportArgumentType]
    except unit_cache._STORE_ERRORS:
        return ("raised", "unreadable")
    except _RECORD_FAILURES:
        return ("raised", "failed")
    return _parsed_value(parsed)


def _prior_value(record: object) -> object:
    """A `prior_record_or_none` result as a `prior-record` line holds it: the record, None, or `("unheld",)` for a record whose class, duplicate group or address the Rust record does not hold."""
    if record is None:
        return None
    assert isinstance(record, unit_cache.PriorRecord), f"prior_record_or_none returned {record!r:.80}"
    held = (
        type(record.class_id) is str
        and _held_optional_text(record.duplicate_group)
        and _held_address(record.address)
        and _held_int(record.inflated)
    )
    return record if held else ("unheld",)


class _KeyProbe:
    """A stand-in for the `columns.KeyIndex` that `unit_cache.named_ordinal` asks, answering with the key it is asked for, so a `named` line holds the key a store line names without the build's index."""

    def find(self, key: bytes) -> bytes:
        return bytes(key)


def _named_key(line: bytes) -> bytes | None:
    return _NAMED_ORDINAL(_KeyProbe(), line)  # pyright: ignore[reportArgumentType, reportReturnType]


def _records_parsed(args: tuple, kwargs: dict) -> Finisher:
    """A `unit_cache._parsed_cached_unit` call, with the record it gave."""
    arguments = _PARSED_CACHED_UNIT_SIGNATURE.bind(*args, **kwargs).arguments
    inputs = (arguments["record"], frozenset(arguments["trusted"]))
    return lambda result: _records_line("run", "parsed", inputs, _parsed_value(result))


def _records_lined(args: tuple, kwargs: dict) -> Finisher:
    """A `unit_cache.record_line` call, with the line it wrote."""
    record = _RECORD_LINE_SIGNATURE.bind(*args, **kwargs).arguments["record"]
    return lambda result: _records_line("run", "record-line", record, result)


def _records_sliced(args: tuple, kwargs: dict) -> Finisher:
    """A `unit_cache.prior_record_or_none` call, with the record it sliced."""
    line = bytes(_PRIOR_RECORD_SIGNATURE.bind(*args, **kwargs).arguments["line"])
    return lambda result: _records_line("run", "prior-record", line, _prior_value(result))


def _records_named(args: tuple, kwargs: dict) -> Finisher:
    """A `unit_cache.named_ordinal` call, with the key the line names, which the recorder reads with the same function over `_KeyProbe`."""
    line = bytes(_NAMED_ORDINAL_SIGNATURE.bind(*args, **kwargs).arguments["line"])
    named = _named_key(line)
    return lambda _result: _records_line("run", "named", line, named)


def _records_called(form: str) -> Recorder:
    """A one-argument `unit_cache` call (`id_word`, `primary_units_crc`, `is_content_id`), recorded under `form` with its argument and what it gave."""

    def record(args: tuple, kwargs: dict) -> Finisher:
        (value,) = (*args, *kwargs.values())
        return lambda result: _records_line("run", form, value, result)

    return record


def _synthetic_record() -> dict:
    """A store record in `to_record`'s order with every part a line can hold: deltas for two configurations, a judged pair, junctions with their rects, primary units with and without an id, a mismatch line of non-ASCII text, quotes, a backslash and control characters, and a deflated fragment's four-element address in `RECORDS_PART`."""
    return {
        "key": "0123456789abcdef" * 4,
        "id": "u-c36hWTyMBQB",
        "class": "a-class",
        "content_key": "d177b592" * 8,
        "slim": False,
        "ink_identical": False,
        "picture_identical": False,
        "junior_equivalent": False,
        "ink_deltas": {"default": "d-34947e8c5c7e", "ss03": "d-ad268abf61a7"},
        "diffs_digest": "758a49a9fc8b39929a64a42262b19fc93d93d8ea",
        "cluster": "c-33bad1c7",
        "change_group": "",
        "ledger_class": "",
        "pair_codepoints": [0, 1],
        "proj": {
            "pair": [0, 1],
            "after_spans": [[0, 1], [1, 2]],
            "after_cells": ["qsMay/loop/None/x-height/", "·Pea 𝄞"],
            "after_junctions": ["y5"],
            "before_spans": [[0, 2]],
            "before_glyphs": ["qsMay"],
            "before_junctions": [],
        },
        "junctions": [
            {
                "pair": [1, 2],
                "before": {"x_min": 300, "x_max": 750, "advance_total": 750},
                "after": {"x_min": 300, "x_max": 650, "advance_total": 650},
            }
        ],
        "mismatches": ['·Tea ~b~ ·Oy: "quoted" \\ \x01\x7f'],
        "duplicate_group": "e-f8P2bEFApF9",
        "exemplar": False,
        "no_verdict": False,
        "primary_units": [[None, False], ["u-DuyicL6fVGo", True]],
        "policy_file": "glyph_data/runes/qsMay.yaml",
        "address": [RECORDS_PART, 1, 5064, 9000],
    }


def _record_bytes(record: Mapping[str, object]) -> bytes:
    return (json.dumps(record) + "\n").encode()


def _store_record_lines(source: BuildSource) -> list[bytes]:
    """The record lines of the dump's prior store in its materialized order, or none when the prior holds no store that reads."""
    try:
        opened = unit_cache.open_store(source.out / DUMP_PRIOR)
    except unit_cache.StoreUnreadable:
        return []
    if opened is None:
        return []
    try:
        return [line for _place, line in opened.lines()]
    finally:
        opened.close()


def _sampled_records(lines: Sequence[bytes]) -> list[bytes]:
    """The first of the store's lines and the first that holds each part a record can hold or leave out: junctions, primary units, mismatch lines, no judged pair, a slim fragment, no address and a deflated fragment's address."""
    tests: tuple[Callable[[dict], bool], ...] = (
        lambda record: True,
        lambda record: bool(record["junctions"]),
        lambda record: bool(record["primary_units"]),
        lambda record: bool(record["mismatches"]),
        lambda record: record["pair_codepoints"] is None,
        lambda record: bool(record["slim"]),
        lambda record: record["address"] is None,
        lambda record: record["address"] is not None and len(record["address"]) == 4,
    )
    records = [json.loads(line) for line in lines]
    chosen = {next((line for line, record in zip(lines, records) if test(record)), None) for test in tests}
    return [line for line in lines if line in chosen]


def _cached_unit_of(record: Mapping) -> unit_cache.CachedUnit:
    """The `CachedUnit` whose line (`record_line`) is the line `record` was read from."""
    address = record["address"]
    pair = record["pair_codepoints"]
    return unit_cache.CachedUnit(
        key=record["key"],
        prior_id=record["id"],
        prior_class=record["class"],
        content_key=record["content_key"],
        slim=record["slim"],
        address=None if address is None else (address[0], address[1], address[2]),
        ink_identical=record["ink_identical"],
        picture_identical=record["picture_identical"],
        junior_equivalent=record["junior_equivalent"],
        ink_deltas=record["ink_deltas"],
        diffs_digest=record["diffs_digest"],
        cluster=record["cluster"],
        change_group=record["change_group"],
        ledger_class=record["ledger_class"],
        pair_codepoints=None if pair is None else (pair[0], pair[1]),
        proj=record["proj"],
        junctions=record["junctions"],
        mismatches=record["mismatches"],
        duplicate_group=record["duplicate_group"],
        exemplar=record["exemplar"],
        no_verdict=record["no_verdict"],
        primary_units=record["primary_units"],
        policy_file=record["policy_file"],
        inflated=address[3] if address is not None and len(address) == 4 else 0,
    )


def _changed(record: Mapping, **changes: object) -> dict:
    """A copy of `record` with `changes` set in place, a `proj` change named `proj_<key>`, and a field changed to `_ABSENT` removed."""
    changed = copy.deepcopy(dict(record))
    for name, value in changes.items():
        target, key = (
            (changed["proj"], name.removeprefix("proj_")) if name.startswith("proj_") else (changed, name)
        )
        if value is _ABSENT:
            target.pop(key, None)
        else:
            target[key] = value
    return changed


def _parsed_edges(record: Mapping) -> Iterator[tuple[object, frozenset[str]]]:
    """Records that put each value `_parsed_cached_unit` reads to every kind Python takes, keeps or raises on, and records whose exceptions race, each with the parts it is parsed as trusting: none, the address's part and another."""
    trusts = (frozenset(), frozenset({RECORDS_PART}), frozenset({"units/other.json"}))
    for trusted in trusts:
        yield record, trusted
    part = RECORDS_PART
    for address in (
        _ABSENT,
        None,
        [],
        0,
        "",
        False,
        [part, 1, 2],
        [part, 1, 2, 0],
        [part, "12", " 3 ", "4_0"],
        [part, "x", 1],
        [part, None, 1],
        [part, 1, 2, [3]],
        [part, 1],
        [part, 1, 2, 3, 4],
        {"a": 1, "b": 2, "c": 3},
        5,
        True,
        [["x"], 1, 2],
        [7, 1, 2],
        [part, 1, "9" * 20],
        [part, 1, "9" * 5000],
    ):
        for trusted in trusts[:2]:
            yield _changed(record, address=address), trusted
    for address in ("abc", "ab9", "a99"):
        for trusted in (frozenset(), frozenset({"a"})):
            yield _changed(record, address=address), trusted
    edges: list[dict[str, object]] = [
        *({name: _ABSENT} for name in record if name != "address"),
        *({f"proj_{name}": _ABSENT} for name in record["proj"]),
        *(
            {"pair_codepoints": pair}
            for pair in (None, [], [5, 6], [5, 6, 7], [5], "ab", {"a": 1}, 7, [True, 2], [[1], 2])
        ),
        {"class": 5},
        {"class": "·Pea 𝄞"},
        {"key": 5},
        {"id": None},
        {"content_key": 5},
        {"slim": 1},
        {"exemplar": None},
        {"no_verdict": "yes"},
        {"duplicate_group": None},
        {"duplicate_group": 5},
        {"policy_file": None},
        {"policy_file": []},
        *({"ink_deltas": deltas} for deltas in ({}, [], None, "x", {"default": 5})),
        {"diffs_digest": 5},
        {"cluster": None},
        {"change_group": []},
        {"ledger_class": "·"},
        *(
            {"primary_units": units}
            for units in (
                [],
                [[None, False]],
                [["u-x", 1]],
                [["u-x", 0, 1]],
                "ab",
                "",
                [[5, True]],
                ["ab"],
                7,
                [7],
                {"ab": 1},
            )
        ),
        *({"mismatches": lines} for lines in ([], "ab", 5, [5], {"k": 1})),
        *({"proj": proj} for proj in ([], "x", None, {})),
        *({"proj_pair": pair} for pair in (None, [], [1, 2], [1, 2, 3], [1], [[1], 2], ["a", "b"])),
        *(
            {f"proj_{name}": spans}
            for name in ("after_spans", "before_spans")
            for spans in ([], [[1, 2], [3]], [[1, [2]]], [{"a": 1}], [5], "ab", [["a", "b"]], 5)
        ),
        *(
            {f"proj_{name}": names}
            for name in ("after_cells", "before_junctions")
            for names in ([], [5], "ab", 5, [None])
        ),
        *(
            {"junctions": junctions}
            for junctions in (
                [],
                5,
                ["x"],
                [{"pair": [1]}],
                [{}],
                [{"pair": [1, 2]}, {}],
                [{"pair": [1]}, {}],
                [{"pair": "ab"}],
            )
        ),
        {"class": 5, "ink_deltas": []},
        {"ink_deltas": [], "cluster": 5},
        {"slim": 1, "pair_codepoints": [5]},
        {"slim": 1, "diffs_digest": 5},
        {"pair_codepoints": [5], "proj_after_cells": _ABSENT},
        {"address": [part, 1], "key": 5},
        {"key": 5, "junctions": [{"pair": [1]}]},
    ]
    for changes in edges:
        yield _changed(record, **changes), frozenset({part})
    yield [1, 2], frozenset()


def _record_line_edges(record: Mapping) -> Iterator[unit_cache.CachedUnit]:
    """`CachedUnit`s that write each part of a line both ways: with and without an address and a deflated length, a judged pair, a duplicate group, a policy file, primary units and deltas, and text of every kind `json.dumps` escapes."""
    cached = _cached_unit_of(record)
    yield cached
    for changes in (
        {"address": None, "inflated": 0},
        {"inflated": 0},
        {"pair_codepoints": None},
        {"duplicate_group": None},
        {"policy_file": None},
        {"primary_units": []},
        {"ink_deltas": {}},
        {"slim": True, "exemplar": True, "no_verdict": True},
        {"prior_class": "·Pea 𝄞", "mismatches": ['\x00\x1f\x7f  "\\/', ""]},
        {"proj": {}, "junctions": []},
    ):
        yield dataclasses.replace(cached, **changes)


def _prior_record_edges(line: bytes) -> Iterator[bytes]:
    """Lines that put `prior_record_or_none`'s slicing to each seam it finds or misses and each value it reads: the address's tail in every shape `record_address` reads or refuses, the patched fields' values and keys, the class and the id."""
    cut = line.rindex(b', "address": ')
    for tail in (
        b"null}\n",
        b'["p", 1, 2]}\n',
        b'["p", 1, 2, 3]}\n',
        b'["p", 1, 2, 0]}\n',
        b'["p", 1]}\n',
        b'["p", 1, 2, 3, 4]}\n',
        b'"abc"}\n',
        b'"a9"}\n',
        b'"a99"}\n',
        b"5}\n",
        b"[[1], 2, 3]}\n",
        b"[null, 2, 3]}\n",
        b'["p", "7", " 8 "]}\n',
        b'["p", "x", 1]}\n',
        b'{"a": 1, "b": 2, "c": 3}}\n',
        b'["p", 1, 99999999999999999999]}\n',
        b'["p", 1, 2]}  \r\n',
        b'["p", 1, 2]}',
        b'["p", 1, 2]',
        b"",
    ):
        yield line[:cut] + b', "address": ' + tail
    text = line.decode("ascii")
    for old, new in (
        (', "duplicate_group": ', ', "duplicate_grp": '),
        (', "policy_file": ', ', "policy_fyle": '),
        ('"class": "a-class"', '"class": 5'),
        ('"class": "a-class"', '"class": "\\u00b7Pea"'),
        ('"class": "a-class"', '"klass": "a-class"'),
        ('"id": "u-c36hWTyMBQB"', '"id": "u-c36hWTyMBQ\\u00b7"'),
        ('"exemplar": false', '"exemplar": 1'),
        ('"exemplar": false', '"exemplar": tru'),
        ('"exemplar": false', '"exemplr": false'),
        ('"no_verdict": false', '"no_verdict": []'),
        ('"duplicate_group": "e-f8P2bEFApF9"', '"duplicate_group": 5'),
        ('"duplicate_group": "e-f8P2bEFApF9"', '"duplicate_group": null'),
        ('"primary_units": [[null, false], ["u-DuyicL6fVGo", true]]', '"primary_units": []'),
        ('"primary_units": [[null, false], ["u-DuyicL6fVGo", true]]', '"primary_units": "\\u00b7"'),
    ):
        yield text.replace(old, new, 1).encode("ascii")
    yield line.replace(b"u-c36hWTyMBQB", "u-c36hWTyMBQ·".encode(), 1)
    yield b'{"key": "00"}\n'
    yield b""


def _named_edges(line: bytes) -> Iterator[bytes]:
    """Lines whose `key` field is every kind `named_ordinal` reads or refuses: long, short and odd runs of hex digits, uppercase, other letters, non-ASCII bytes, and lines without the prefix."""
    prefix = unit_cache._KEY_PREFIX
    digits = line[len(prefix) : len(prefix) + 64]
    yield from (b"", prefix, prefix + digits.upper(), prefix + digits[:62], prefix + digits[:63])
    yield from (prefix + digits[:62] + b'"', prefix + b"g" * 64, prefix + "é".encode() * 32)
    yield from (b'{"key":"' + digits, b" " + prefix + digits, prefix + digits + b"ff")


def _store_records_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `store-records` stage's generated lines: the synthetic record (`_synthetic_record`) and a sample of the dump's prior store (`_sampled_records`), each parsed, written, sliced and named, with each form's edges over the synthetic record (`_parsed_edges`, `_record_line_edges`, `_prior_record_edges`, `_named_edges`), and ids, primary units and values at the edges of `id_word`, `primary_units_crc` and `is_content_id`."""
    synthetic = _synthetic_record()
    lines = [_record_bytes(synthetic), *_sampled_records(_store_record_lines(source))]
    records = [json.loads(line) for line in lines]
    for record in records:
        address = record["address"]
        for trusted in (frozenset(), frozenset({address[0]} if address else ())):
            yield _records_line("generated", "parsed", (record, trusted), _parsed_outcome(record, trusted))
    for record, trusted in _parsed_edges(synthetic):
        yield _records_line("generated", "parsed", (record, trusted), _parsed_outcome(record, trusted))
    for cached in (*map(_cached_unit_of, records[1:]), *_record_line_edges(synthetic)):
        yield _records_line("generated", "record-line", cached, _RECORD_LINE(cached))
    for line in (*lines, *_prior_record_edges(lines[0])):
        yield _records_line("generated", "prior-record", line, _prior_value(_PRIOR_RECORD_OR_NONE(line)))
    for line in (*lines, *_named_edges(lines[0])):
        yield _records_line("generated", "named", line, _named_key(line))
    top = unit_cache.base58_64("f" * 16)
    above = top[:-1] + unit_cache.BASE58_ALPHABET[unit_cache.BASE58_ALPHABET.index(top[-1]) + 1]
    ids = [record["id"] for record in records]
    for unit_id in (
        *ids,
        "u-11111111111",
        f"u-{top}",
        f"u-{above}",
        "u-zzzzzzzzzzz",
        "e-c36hWTyMBQB",
        "u-c36hWTyMBQ",
        "u-c36hWTyMBQBB",
        "u-c36hWTyMBQ0",
        "u-c36hWTyMBQ·",
        "U-c36hWTyMBQB",
        "u_c36hWTyMBQB",
        "",
    ):
        yield _records_line("generated", "id-word", unit_id, _ID_WORD(unit_id))
    for units in (
        *(record["primary_units"] for record in records),
        [],
        [[None, False]],
        [["·", False]],
        None,
        "",
        0,
        "x",
        [[]],
        {"a": [1]},
    ):
        crc = _PRIMARY_UNITS_CRC(units)  # pyright: ignore[reportArgumentType]
        yield _records_line("generated", "primary-units-crc", units, crc)
    for value in (
        *ids,
        "u-c36hWTyMBQB\n",
        "u-c36hWTyMBQB\n\n",
        "e-f8P2bEFApF9",
        "x-f8P2bEFApF9",
        "u-c36hWTyMBQ0",
        "u-c36hWTyMBQ",
        "u-c36hWTyMBQ·",
        5,
        None,
        ["u-c36hWTyMBQB"],
    ):
        yield _records_line("generated", "content-id", value, _IS_CONTENT_ID(value))


STORE_LOAD_STAGE = "store-load"
# The `UnitStore` methods the `store-load` stage follows on each store from its making, by the op its lines name them: every method the build calls that changes a store, and `input_key_index`.
STORE_FOLLOWED = {
    "__init__": "new",
    "load_projection": "load-projection",
    "load_cached": "load-cached",
    "add_prior_record": "add-prior-record",
    "seal_prior_records": "seal",
    "set_prior_line": "set-prior-line",
    "input_key_index": "input-key-index",
    "set_primary_units": "set-primary-units",
    "set_written_span": "set-written-span",
    "set_written_part": "set-written-part",
    "set_policy_file": "set-policy-file",
    "set_config_note": "set-config-note",
}
_UNIT_STORE_SIGNATURES = {name: inspect.signature(getattr(UnitStore, name)) for name in STORE_FOLLOWED}
_UNIT_STORE_LOCK = threading.Lock()
# What a store call raises that a `store-load` line records as its value.
_UNIT_STORE_RAISES = (
    ValueError,
    IndexError,
    KeyError,
    TypeError,
    OverflowError,
    SystemExit,
    columns.RepeatedKey,
)
# The fields of a primary-unit projection's rows, as `unit_store.PrimaryUnitRows` names them.
_UNIT_STORE_ROWS = (
    "codepoint_values",
    "ink_identical",
    "picture_identical",
    "pair",
    "after_spans",
    "after_cells",
    "after_junctions",
    "before_spans",
    "before_glyphs",
    "before_junctions",
    "junction_pairs",
)
# How many units with junctions, and how many units in all, the scripted continuation of a build's store changes before reading them again.
STORE_SCRIPT_UNITS = 4


@dataclass
class _UnitStoreTrace:
    """A unit store the `store-load` stage follows: the store, held so that its `id` is not reused while the build runs; its name in the lines, `run:store:<serial>`; the thread that made it, the only one whose calls on it the stage takes in order; and each call it recorded with the value the call was given, which the stage's generated lines replay into a store of their own."""

    held: UnitStore
    name: str
    thread: int
    calls: list[tuple[tuple, object]] = field(default_factory=list)


# The stores the stage follows, by `id`, for the length of a build, and every store it followed, in making order, until its generated lines replay them (`_unit_store_following`).
_UNIT_STORE_TRACES: dict[int, _UnitStoreTrace] = {}
_UNIT_STORE_RUNS: list[_UnitStoreTrace] = []


@contextlib.contextmanager
def _unit_store_following() -> Iterator[None]:
    with _UNIT_STORE_LOCK:
        _UNIT_STORE_TRACES.clear()
        _UNIT_STORE_RUNS.clear()
    try:
        yield
    finally:
        with _UNIT_STORE_LOCK:
            _UNIT_STORE_TRACES.clear()


def _projection_value(projection: Any) -> Record:
    """A recomputed unit's projection as a `store-load` line holds it: the fields `unit_store.RecomputedProjection` names, the ink deltas as pairs and the rows as `unit_store.PrimaryUnitRows` names them, each value as the projection holds it."""
    rows = projection.primary_unit_projection
    return Record(
        "Recomputed",
        (
            ("ordinal", projection.ordinal),
            ("unit_id", projection.unit_id),
            ("input_key", projection.input_key),
            ("content_key", projection.content_key),
            ("ink_identical", projection.ink_identical),
            ("picture_identical", projection.picture_identical),
            ("junior_equivalent", projection.junior_equivalent),
            ("ink_deltas", [tuple(pair) for pair in projection.ink_deltas]),
            ("diffs_digest", projection.diffs_digest),
            ("cluster", projection.cluster),
            ("change_group", projection.change_group),
            ("ledger_class", projection.ledger_class),
            ("pair_codepoints", projection.pair_codepoints),
            (
                "primary_unit_projection",
                Record("PrimaryUnitRows", tuple((name, getattr(rows, name)) for name in _UNIT_STORE_ROWS)),
            ),
            ("junction_rects", [tuple(rect) for rect in projection.junction_rects]),
            ("mismatches", list(projection.mismatches)),
        ),
    )


def _unit_store_encoded(call: tuple) -> tuple:
    if call[0] == "load-projection":
        return (call[0], _projection_value(call[1]), *call[2:])
    return call


def _unit_store_raised(error: BaseException) -> tuple:
    """A refused call's value: `("raised", "RepeatedKey", first, second)`, or the exception's class and message."""
    if isinstance(error, columns.RepeatedKey):
        return ("raised", "RepeatedKey", error.first, error.second)
    return ("raised", type(error).__name__, str(error))


def _written_value(store: UnitStore, ordinal: int) -> tuple:
    return ("written", store.written_address(ordinal), store.written_inflated(ordinal))


def _store_reading(store: UnitStore, ordinal: int) -> dict[str, object]:
    """What the stage reads of one row after a call: every accessor that takes only the ordinal, by name, the id word as 16 hex digits."""
    return {
        "loaded": store.loaded(ordinal),
        "flags": store.flags(ordinal),
        "invisible": store.invisible(ordinal),
        "machine_flags": store.machine_flags(ordinal),
        "machine_approved": store.machine_approved(ordinal),
        "machine_check": store.machine_check(ordinal),
        "id_word": f"{store.id_word(ordinal):016x}",
        "unit_id": store.unit_id(ordinal),
        "content_key": store.content_key_hex(ordinal),
        "input_key": store.input_key_hex(ordinal),
        "diffs_digest": store.diffs_digest(ordinal),
        "cluster": store.cluster(ordinal),
        "change_group": store.change_group(ordinal),
        "ledger_class": store.ledger_class(ordinal),
        "pair_codepoints": store.pair_codepoints(ordinal),
        "cell_pair": store.cell_pair(ordinal),
        "cached_class": store.cached_class(ordinal),
        "cached_duplicate_group": store.cached_duplicate_group(ordinal),
        "policy_file": store.policy_file(ordinal),
        "config_note": store.config_note(ordinal),
        "ink_deltas": store.ink_deltas(ordinal),
        "codepoints": store.codepoints(ordinal),
        "mismatches": store.mismatches(ordinal),
        "source": store.source(ordinal),
        "written": _written_value(store, ordinal),
        "junction_count": store.junction_count(ordinal),
        "junction_pairs": store.junction_pairs(ordinal),
        "primary_unit_projection": store.primary_unit_projection(ordinal),
        "projection": store.projection(ordinal),
        "primary_unit_projection_record": store.primary_unit_projection_record(ordinal),
        "junction_rects": store.junction_rects(ordinal),
        "primary_unit_ordinals": store.primary_unit_ordinals(ordinal),
        "primary_units": store.primary_units(ordinal),
        "primary_units_record": store.primary_units_record(ordinal),
        "cached_primary_units": store.cached_primary_units(ordinal),
        "shard_length": store.shard_length(ordinal),
        "prior_line": store.prior_line(ordinal),
        "prior_address": store.prior_address(ordinal),
        "prior_fragment": store.prior_fragment(ordinal),
    }


def _sealed_records(store: UnitStore) -> list[tuple]:
    """The store's kept prior records in its order: each one's id word as 16 hex digits, its part, start, length, inflated length, class, duplicate group, flag bits and primary units' CRC-32."""
    return [
        (
            f"{store._by_id_words[at]:016x}",
            store._by_id_parts[store._by_id_part[at]],
            store._by_id_start[at],
            store._by_id_len[at],
            store._by_id_inflated[at],
            store._table[store._by_id_class[at]],
            store._optional(store._by_id_group[at]),
            store._by_id_bits[at],
            store._by_id_units[at],
        )
        for at in range(len(store._by_id_words))
    ]


def _unit_store_invoke(store: UnitStore, call: tuple) -> object:
    """Make one call of a `store-load` trace on `store` and return what it gave."""
    op, *args = call
    if op == "load-projection":
        projection, no_verdict, ordinal, address, length = args
        return store.load_projection(
            projection, no_verdict=no_verdict, ordinal=ordinal, address=address, length=length
        )
    if op == "load-cached":
        ordinal, cached, codepoints, found = args
        return store.load_cached(ordinal, cached, codepoints=codepoints, found=found)
    if op == "patched":
        ordinal, class_id, group, exemplar, no_verdict = args
        patched = {
            "class_id": class_id,
            "duplicate_group": group,
            "exemplar": exemplar,
            "no_verdict": no_verdict,
        }
        return (
            store.cached_as_is(ordinal, **patched),
            store.predicted_unchanged(ordinal, **patched),
            unit_cache.record_line(store.cached_unit(ordinal, **patched)),
        )
    if op == "read":
        return None
    if op == "primary-unit-ids":
        return sorted(store.primary_unit_ids())
    if op == "windows":
        return list(store.windows())
    methods: dict[str, Callable[..., object]] = {
        "add-prior-record": store.add_prior_record,
        "seal": store.seal_prior_records,
        "set-prior-line": store.set_prior_line,
        "input-key-index": store.input_key_index,
        "set-input-key": store.set_input_key,
        "set-primary-units": store.set_primary_units,
        "set-written-span": store.set_written_span,
        "set-written-part": store.set_written_part,
        "set-written-address": store.set_written_address,
        "set-policy-file": store.set_policy_file,
        "set-config-note": store.set_config_note,
        "rebase-source": store.rebase_source,
        "index": store.index,
        "ordinal-of": store.ordinal_of,
        "prior-record-count": store.prior_record_count,
        "emptied": store.emptied,
    }
    return methods[op](*args)


def _unit_store_after(store: UnitStore, call: tuple, result: Any) -> object:
    """A store call's value, from the store after it and what it gave: a load's row and what `_store_reading` reads of it, a setter's field read back, and the index, the kept prior records and the store an emptied one is, as the stage writes each."""
    op = call[0]
    ordinal: int = call[1] if len(call) > 1 else -1
    if op in ("load-projection", "load-cached"):
        return ("loaded", result, _store_reading(store, result))
    if op == "add-prior-record":
        return ("added", store.prior_record_count())
    if op == "seal":
        return ("sealed", _sealed_records(store))
    if op == "set-prior-line":
        return ("line", store.prior_line(ordinal))
    if op == "input-key-index":
        return ("built", _KEY_INDEX_BYTES(result))
    if op == "set-input-key":
        return ("input-key", store.input_key_hex(ordinal))
    if op == "set-primary-units":
        return ("set", store.primary_unit_ordinals(ordinal))
    if op in ("set-written-span", "set-written-part", "set-written-address"):
        return _written_value(store, ordinal)
    if op == "set-policy-file":
        return ("policy", store.policy_file(ordinal))
    if op == "set-config-note":
        return ("note", store.config_note(ordinal))
    if op == "rebase-source":
        return ("source", store.source(ordinal))
    if op == "index":
        words, ordinals = result
        return ("indexed", [f"{word:016x}" for word in words], list(ordinals))
    if op == "ordinal-of":
        return ("ordinal", result)
    if op == "read":
        return _store_reading(store, ordinal)
    if op == "patched":
        return ("patched", *result)
    if op == "emptied":
        return ("emptied", result.n, bytes(result._input_keys), sum(result._loaded))
    return result


def _unit_store_run(store: UnitStore | None, call: tuple) -> tuple[object, UnitStore | None]:
    """Make one call of a `store-load` trace outside the build: its value, a refusal's `_unit_store_raised`, and the store the trace follows after it, a new one for `new`. A key index it builds is released, as the build releases its own, so the store's input-key column can be copied."""
    if call[0] == "new":
        _op, n, keys = call
        try:
            made = UnitStore(n, input_keys=None if keys is None else bytearray(keys))
        except _UNIT_STORE_RAISES as error:
            return _unit_store_raised(error), None
        return ("made", n), made
    if store is None:
        raise RuntimeError(f"a store-load trace calls {call[0]} before it makes a store")
    try:
        result = _unit_store_invoke(store, call)
    except _UNIT_STORE_RAISES as error:
        return _unit_store_raised(error), store
    value = _unit_store_after(store, call, result)
    if isinstance(result, columns.KeyIndex):
        result.release()
    return value, store


def _unit_store_line(name: str, index: int, call: tuple, value: object) -> dict[str, object]:
    return {
        "key": f"{name}:{index}",
        "form": "store",
        "input": (name, index, _unit_store_encoded(call)),
        "value": value,
    }


def _unit_store_step(trace: _UnitStoreTrace, call: tuple, value: object) -> dict[str, object]:
    """The line of a followed store's next call, kept for the trace's replay; a call on another thread than the one that made it is refused, since the calls' order in the lines would not be the order they ran in."""
    with _UNIT_STORE_LOCK:
        if threading.get_ident() != trace.thread:
            raise RuntimeError(f"the store-load stage follows {trace.name}, which a second thread calls")
        index = len(trace.calls)
        trace.calls.append((call, value))
    return _unit_store_line(trace.name, index, call, value)


def _unit_store_followed(method: str) -> Recorder:
    """Follow a unit store from its making (`__init__`, a `new` line with its size and the input-key column it takes, or None), then each call on it `STORE_FOLLOWED` names, with its arguments in the method's order and what `_unit_store_after` reads of the store after it."""
    op = STORE_FOLLOWED[method]
    signature = _UNIT_STORE_SIGNATURES[method]

    def record(args: tuple, kwargs: dict) -> Finisher | None:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = dict(bound.arguments)
        made = arguments.pop("self")
        if method == "__init__":
            keys = arguments["input_keys"]
            call: tuple = ("new", arguments["n"], None if keys is None else bytes(keys))

            def finish(_result: object) -> Mapping[str, object]:
                with _UNIT_STORE_LOCK:
                    trace = _UnitStoreTrace(made, f"run:store:{len(_UNIT_STORE_RUNS)}", threading.get_ident())
                    _UNIT_STORE_TRACES[id(made)] = trace
                    _UNIT_STORE_RUNS.append(trace)
                return _unit_store_step(trace, call, ("made", call[1]))

            return finish
        trace = _UNIT_STORE_TRACES.get(id(made))
        if trace is None or trace.held is not made:
            return None
        values = list(arguments.values())
        if method == "set_primary_units":
            values[1] = [tuple(entry) for entry in values[1]]
        followed = (op, *values)
        return lambda result: _unit_store_step(trace, followed, _unit_store_after(made, followed, result))

    return record


def _patched_cases(store: UnitStore, ordinal: int) -> list[tuple]:
    """The class, duplicate group, exemplar and exemption a `patched` line asks a row's record under: the ones its fragment was written with, from its store record or its kept prior record, then a moved class and group with the exemplar flipped, then the exemption flipped."""
    flags = store.flags(ordinal)
    index = None if flags.cached else store._prior_index(ordinal)
    if flags.cached:
        own = (
            store.cached_class(ordinal) or "",
            store.cached_duplicate_group(ordinal),
            flags.exemplar,
            flags.no_verdict,
        )
    elif index is not None:
        bits = store._by_id_bits[index]
        own = (
            store._table[store._by_id_class[index]],
            store._optional(store._by_id_group[index]),
            bool(bits & unit_store.PRIOR_EXEMPLAR),
            bool(bits & unit_store.PRIOR_NO_VERDICT),
        )
    else:
        own = ("a-class", None, False, False)
    class_id, group, exemplar, no_verdict = own
    return [
        own,
        (f"{class_id}-moved", None if group else "e-moved", not exemplar, no_verdict),
        (class_id, group, exemplar, not no_verdict),
    ]


def _scripted_target(store: UnitStore, ordinal: int, at: int) -> tuple[int | str | None, bool]:
    """A primary unit the scripted continuation assigns a row's junction: none, an ordinal suppressed or not, or an id, by the junction's place."""
    n = store.n
    kind = (ordinal + at) % 4
    if kind == 0:
        return (None, False)
    if kind == 1:
        return ((ordinal * 7 + at + 1) % n, True)
    if kind == 2:
        return (store.unit_id((ordinal * 3 + at + 2) % n), False)
    return ((ordinal + at) % n, False)


def _store_script(store: UnitStore) -> Iterator[tuple]:
    """The calls the stage makes on a replayed store once the build's own end: the id index and every id's ordinal beside ids no unit has, the kept prior records' count, the primary units' ids and the windows, every row read and its record asked under `_patched_cases`; then some rows' primary units, written addresses, policy files, config notes, prior lines, sources and input keys set and read again, and the store emptied; and last the refusals, some of which leave a row partly written, as Python does."""
    n = store.n
    yield ("index",)
    for ordinal in range(n):
        yield ("ordinal-of", store.unit_id(ordinal))
    top = unit_cache.base58_64("f" * 16)
    for unit_id in ("u-11111111111", f"u-{top}", "e-11111111111", "", "u-1111111111", "u-1111111111l"):
        yield ("ordinal-of", unit_id)
    yield ("prior-record-count",)
    yield ("primary-unit-ids",)
    yield ("windows",)
    for ordinal in range(n):
        yield ("read", ordinal)
        for case in _patched_cases(store, ordinal):
            yield ("patched", ordinal, *case)
    yield ("input-key-index",)
    junctioned = [ordinal for ordinal in range(n) if store.junction_count(ordinal)][:STORE_SCRIPT_UNITS]
    for ordinal in junctioned:
        yield (
            "set-primary-units",
            ordinal,
            [_scripted_target(store, ordinal, at) for at in range(store.junction_count(ordinal))],
        )
        yield ("read", ordinal)
        yield ("patched", ordinal, *_patched_cases(store, ordinal)[0])
    yield ("primary-unit-ids",)
    for ordinal in range(min(n, STORE_SCRIPT_UNITS)):
        yield (
            "set-written-address",
            ordinal,
            (f"units/class-{ordinal % 2}.json", 11 * ordinal, 100 + ordinal),
            0 if ordinal % 2 else 9000 + ordinal,
        )
        yield ("set-policy-file", ordinal, (None, "", "glyph_data/runes/qsMay.yaml", "·Pea")[ordinal % 4])
        yield ("set-config-note", ordinal, ("ss01 only", None, "", "·")[ordinal % 4])
        yield ("set-prior-line", ordinal, 3 * ordinal)
        if store.source(ordinal) is not None:
            yield ("rebase-source", ordinal, 5 * ordinal)
        yield ("set-input-key", ordinal, store.input_key_hex(ordinal).upper())
        yield ("read", ordinal)
        for case in _patched_cases(store, ordinal):
            yield ("patched", ordinal, *case)
    yield ("emptied",)
    if junctioned:
        ordinal = junctioned[0]
        rest = [(None, False)] * (store.junction_count(ordinal) - 1)
        yield ("set-primary-units", ordinal, [(None, False), *rest, (None, False)])
        yield ("set-primary-units", ordinal, [*rest, (n + 3, False)])
        yield ("set-primary-units", ordinal, [*rest, (-1, True)])
        yield ("set-primary-units", ordinal, [*rest, ("u-11111111111", True)])
        yield ("read", ordinal)
    yield ("set-written-span", 0, 0, 2**32, 0)
    yield ("set-written-span", 0, -1, 1, 0)
    yield ("set-input-key", 0, "zz")
    yield ("set-input-key", 0, "ab")
    yield ("rebase-source", 0, -1)
    yield ("set-prior-line", 0, -1)
    yield ("read", 0)


def _store_continued(trace: _UnitStoreTrace) -> Iterator[Mapping[str, object]]:
    """Replay a store the build made, checking that each call gives the value the build's gave, and, when every row of it was loaded, continue its trace with `_store_script`'s calls."""
    store: UnitStore | None = None
    for call, value in trace.calls:
        replayed, store = _unit_store_run(store, call)
        if replayed != value:
            raise RuntimeError(
                f"replaying {trace.name}'s {call[0]} gave {replayed!r:.300}, not the build's {value!r:.300}"
            )
    if store is None or store.n == 0 or not all(store._loaded):
        return
    index = len(trace.calls)
    for call in _store_script(store):
        value, store = _unit_store_run(store, call)
        yield _unit_store_line(trace.name, index, call, value)
        index += 1


STORE_CONTENT_KEY = hashlib.sha256(b"store-load").hexdigest()
STORE_INPUT_KEY = hashlib.sha256(b"store-load input").hexdigest()
STORE_EDGE = {"x_min": 300, "x_max": 750, "advance_total": 750}


@dataclass(frozen=True)
class _StoreRows:
    """A primary-unit projection's rows as the units engine's reply holds them (`units_exec.RustPrimaryUnitRows`), for the stage's generated loads."""

    codepoint_values: tuple[int, ...] = (0xE650, 0xE670)
    ink_identical: bool = False
    picture_identical: bool = False
    pair: list[int] | None = field(default_factory=lambda: [0, 1])
    after_spans: list[list[int]] = field(default_factory=lambda: [[0, 1], [1, 2]])
    after_cells: list[str] = field(default_factory=lambda: ["qsMay/loop/None/x-height/", "·Pea 𝄞"])
    after_junctions: list[str] = field(default_factory=lambda: ["y5"])
    before_spans: list[list[int]] = field(default_factory=lambda: [[0, 2]])
    before_glyphs: list[str] = field(default_factory=lambda: ["qsMay"])
    before_junctions: list[str] = field(default_factory=list)
    junction_pairs: list[list[int]] = field(default_factory=lambda: [[1, 2]])


@dataclass(frozen=True)
class _StoreProjection:
    """A recomputed unit's projection as the units engine's reply holds it (`units_exec.RustProjection`), for the stage's generated loads."""

    ordinal: int = 0
    unit_id: str = unit_cache.unit_id_for(STORE_CONTENT_KEY)
    input_key: str = STORE_INPUT_KEY
    content_key: str = STORE_CONTENT_KEY
    ink_identical: bool = False
    picture_identical: bool = False
    junior_equivalent: bool = False
    ink_deltas: list[tuple[str, str]] = field(
        default_factory=lambda: [("default", "d-34947e8c5c7e"), ("ss03", "d-ad268abf61a7")]
    )
    diffs_digest: str = "758a49a9fc8b39929a64a42262b19fc93d93d8ea"
    cluster: str = "c-33bad1c7"
    change_group: str = ""
    ledger_class: str = ""
    pair_codepoints: list[int] | None = field(default_factory=lambda: [0, 1])
    primary_unit_projection: _StoreRows = field(default_factory=_StoreRows)
    junction_rects: list[tuple[list[int], dict, dict]] = field(
        default_factory=lambda: [
            ([1, 2], dict(STORE_EDGE), {"x_min": 300, "x_max": 650, "advance_total": 650})
        ]
    )
    mismatches: list[str] = field(default_factory=lambda: ['·Tea ~b~ ·Oy: "quoted" \\ \x01\x7f'])


def _keyed(content: str, **changes: Any) -> _StoreProjection:
    """The generated projection under the content key the sha256 of `content` is, whose id it takes, with `changes`."""
    key = hashlib.sha256(content.encode()).hexdigest()
    return dataclasses.replace(
        _StoreProjection(), content_key=key, unit_id=unit_cache.unit_id_for(key), **changes
    )


def _rows(**changes: Any) -> _StoreProjection:
    """The generated projection with `changes` made to its rows."""
    return _StoreProjection(primary_unit_projection=dataclasses.replace(_StoreRows(), **changes))


def _edged(edge: object, pair: list[int] | None = None) -> list[tuple[list[int], object, dict]]:
    """The generated projection's junction rects with `edge` as the junction's after edge, at `pair`."""
    return [([1, 2] if pair is None else pair, dict(STORE_EDGE), edge)]  # pyright: ignore[reportReturnType]


def _keys(*keys: str | None) -> bytes:
    return b"".join(bytes(32) if key is None else bytes.fromhex(key) for key in keys)


def _cached_record(**changes: object) -> unit_cache.ParsedCachedUnit:
    """The generated store record under `STORE_CONTENT_KEY`, parsed as the plan parses it with its part trusted, with `changes` made to the record first."""
    record: dict[str, object] = {
        "key": STORE_INPUT_KEY,
        "id": unit_cache.unit_id_for(STORE_CONTENT_KEY),
        "class": "a-class",
        "content_key": STORE_CONTENT_KEY,
        "slim": False,
        "ink_identical": False,
        "picture_identical": False,
        "junior_equivalent": False,
        "ink_deltas": {"default": "d-34947e8c5c7e", "ss03": "d-ad268abf61a7"},
        "diffs_digest": "758a49a9fc8b39929a64a42262b19fc93d93d8ea",
        "cluster": "c-33bad1c7",
        "change_group": "",
        "ledger_class": "",
        "pair_codepoints": [0, 1],
        "proj": {
            "pair": [0, 1],
            "after_spans": [[0, 1], [1, 2]],
            "after_cells": ["qsMay/loop/None/x-height/", "·Pea 𝄞"],
            "after_junctions": ["y5"],
            "before_spans": [[0, 2]],
            "before_glyphs": ["qsMay"],
            "before_junctions": [],
        },
        "junctions": [
            {
                "pair": [1, 2],
                "before": dict(STORE_EDGE),
                "after": {"x_min": 300, "x_max": 650, "advance_total": 650},
            }
        ],
        "mismatches": ["·Pea"],
        "duplicate_group": "e-f8P2bEFApF9",
        "exemplar": False,
        "no_verdict": False,
        "primary_units": [["u-DuyicL6fVGo", True]],
        "policy_file": "glyph_data/runes/qsMay.yaml",
        "address": [RECORDS_PART, 1, 5064, 9000],
    }
    record.update(changes)
    return _PARSED_CACHED_UNIT(record, frozenset({RECORDS_PART}), {}, False)


def _prior_record(content: str, **changes: object) -> unit_cache.PriorRecord:
    """A kept prior record for the unit under the content key the sha256 of `content` is, with `changes`."""
    fields: dict[str, Any] = {
        "unit_id": unit_cache.unit_id_for(hashlib.sha256(content.encode()).hexdigest()),
        "class_id": "a-class",
        "duplicate_group": None,
        "exemplar": False,
        "no_verdict": False,
        "primary_units": unit_cache.primary_units_crc([[None, False]]),
        "address": ("units/a-class.x000.json", 10, 20),
        "inflated": 900,
    }
    fields.update(changes)
    return unit_cache.PriorRecord(**fields)


def _projection_refusals() -> Iterator[tuple[str, _StoreProjection, int | None, object, int]]:
    """The generated loads of one projection into a fresh store, each its name, the projection, the ordinal, the address and the length: one at each refusal `load_projection` makes, each column's width at both ends, and the keys, rects and deltas in each shape Python reads or refuses."""
    base = _StoreProjection()
    fragment = unit_cache.PriorFragment(
        "units/a-class.x000.json", 3, 40, base.unit_id, base.content_key, byte_copied=True, inflated=900
    )
    key = STORE_CONTENT_KEY
    yield "no-ordinal", dataclasses.replace(base, ordinal=-1), None, None, 0
    yield "ink-flags", dataclasses.replace(base, ink_identical=True), None, None, 0
    yield "outside", base, 2, None, 0
    yield "negative", base, -1, None, 0
    yield "content-key-hex", dataclasses.replace(base, content_key="zz" + key[2:]), None, None, 0
    yield "content-key-odd", dataclasses.replace(base, content_key=key + "a"), None, None, 0
    yield "content-key-short", dataclasses.replace(base, content_key=key[:62]), None, None, 0
    yield "content-key-upper", dataclasses.replace(base, content_key=key.upper()), None, None, 0
    yield "content-key-led", dataclasses.replace(base, content_key=" " + key), None, None, 0
    led = dataclasses.replace(base, content_key=" " + key, unit_id=unit_cache.unit_id_for(" " + key))
    yield "content-key-led-id", led, None, None, 0
    yield "content-key-gap", dataclasses.replace(base, content_key=key[:4] + " " + key[4:]), None, None, 0
    yield "input-key-odd", dataclasses.replace(base, input_key="abc"), None, None, 0
    spaced = " ".join(STORE_INPUT_KEY[at : at + 2] for at in range(0, 64, 2))
    yield "input-key-spaced", dataclasses.replace(base, input_key=spaced), None, None, 0
    yield "unit-id", dataclasses.replace(base, unit_id="u-11111111111"), None, None, 0
    yield "after-row", _rows(after_cells=["qsMay/loop/None/x-height/"]), None, None, 0
    yield "before-row", _rows(before_junctions=["x"]), None, None, 0
    yield "rect-count", dataclasses.replace(base, junction_rects=[]), None, None, 0
    yield "rect-pair", dataclasses.replace(
        base, junction_rects=_edged(dict(STORE_EDGE), [2, 1])
    ), None, None, 0
    yield "rect-pair-bool", dataclasses.replace(
        base, junction_rects=_edged(dict(STORE_EDGE), [True, 2])
    ), None, None, 0
    reordered = {"x_max": 750, "x_min": 300, "advance_total": 750}
    yield "rect-keys", dataclasses.replace(base, junction_rects=_edged(reordered)), None, None, 0
    yield "rect-extra-key", dataclasses.replace(
        base, junction_rects=_edged({**STORE_EDGE, "y": 1})
    ), None, None, 0
    named = ["x_min", "x_max", "advance_total"]
    yield "rect-list", dataclasses.replace(base, junction_rects=_edged(named)), None, None, 0
    yield "rect-string", dataclasses.replace(base, junction_rects=_edged("xyz")), None, None, 0
    yield "rect-int", dataclasses.replace(base, junction_rects=_edged(5)), None, None, 0
    for index, value in enumerate(("650", " -7 ", None, "3x", True, 2**31, -(2**31) - 1, [1])):
        edge = {**STORE_EDGE, "x_max": value}
        yield f"rect-value-{index}", dataclasses.replace(base, junction_rects=_edged(edge)), None, None, 0
    yield "pair-wide", dataclasses.replace(base, pair_codepoints=[0, 128]), None, None, 0
    yield "pair-low", dataclasses.replace(base, pair_codepoints=[-129, 0]), None, None, 0
    yield "cell-pair-wide", _rows(pair=[200, 1]), None, None, 0
    yield "codepoint-wide", _rows(codepoint_values=(0x10000,)), None, None, 0
    yield "codepoint-low", _rows(codepoint_values=(-1,)), None, None, 0
    yield "span-wide", _rows(after_spans=[[0, 70000], [1, 2]]), None, None, 0
    yield "span-low", _rows(before_spans=[[-1, 2]]), None, None, 0
    wide_pair = dataclasses.replace(
        _rows(junction_pairs=[[1, 300]]), junction_rects=_edged(dict(STORE_EDGE), [1, 300])
    )
    yield "junction-pair-wide", wide_pair, None, None, 0
    many = [(f"c{index}", "d") for index in range(256)]
    yield "deltas-many", dataclasses.replace(base, ink_deltas=many), None, None, 0
    yield "codepoints-many", _rows(codepoint_values=tuple(range(256))), None, None, 0
    rows_many = _rows(
        after_spans=[[index, index + 1] for index in range(256)],
        after_cells=[f"cell{index}" for index in range(256)],
        after_junctions=[f"y{index}" for index in range(255)],
    )
    yield "rows-many", rows_many, None, None, 0
    yield "length-wide", base, None, None, 2**32
    yield "length-low", base, None, None, -1
    yield "address-low", base, None, ("arena", -1, 4), 0
    yield "address-wide", base, None, ("arena", 0, 2**32), 0
    yield "fragment-wide", base, None, dataclasses.replace(fragment, inflated=2**32), 0
    repeated = [("default", "a"), ("ss03", "b"), ("default", "c")]
    yield "deltas-repeated", dataclasses.replace(base, ink_deltas=repeated), None, None, 0
    empty_rows = _StoreRows(
        codepoint_values=(),
        pair=None,
        after_spans=[],
        after_cells=[],
        after_junctions=[],
        before_spans=[],
        before_glyphs=[],
        before_junctions=[],
        junction_pairs=[],
    )
    empty = _keyed(
        "empty",
        ink_deltas=[],
        pair_codepoints=None,
        primary_unit_projection=empty_rows,
        junction_rects=[],
        mismatches=[],
    )
    yield "empty", empty, None, None, 0


def _store_cases() -> Iterator[tuple[str, list[tuple]]]:
    """The generated stores, each its name and its calls: loads of every shape a projection and a record take, read back, asked for their records, given primary units and indexed; each refusal of a load (`_projection_refusals`), of the index and of the prior records; the store's own refusal of a short input-key column and its key index's of a repeated key; and an unloaded row read."""
    base = _StoreProjection()
    fragment = unit_cache.PriorFragment(
        "units/a-class.x000.json", 3, 40, base.unit_id, base.content_key, byte_copied=True, inflated=900
    )
    one, zero = _keyed("one"), _keyed("zero")
    zero_rows = dataclasses.replace(_StoreRows(), ink_identical=True, pair=None)
    yield "projections", [
        ("new", 3, _keys(None, None, STORE_INPUT_KEY)),
        ("load-projection", dataclasses.replace(base, ordinal=2), True, None, ("arena", 7, 40), 120),
        (
            "load-projection",
            _keyed("one", junior_equivalent=True, pair_codepoints=None, mismatches=[]),
            False,
            1,
            dataclasses.replace(fragment, unit_id=one.unit_id, content_key=one.content_key),
            4000,
        ),
        (
            "load-projection",
            _keyed(
                "zero",
                ink_identical=True,
                primary_unit_projection=zero_rows,
                input_key=hashlib.sha256(b"zero").hexdigest(),
            ),
            False,
            0,
            dataclasses.replace(fragment, byte_copied=False, inflated=0),
            0,
        ),
        ("index",),
        *(("ordinal-of", projection.unit_id) for projection in (base, one, zero)),
        ("set-primary-units", 2, [(0, False)]),
        ("set-primary-units", 1, [(zero.unit_id, False)]),
        ("primary-unit-ids",),
        *(("read", ordinal) for ordinal in range(3)),
        *(("patched", ordinal, "a-class", None, False, False) for ordinal in range(3)),
        ("input-key-index",),
        ("windows",),
        ("emptied",),
    ]
    yield "projection-twice", [
        ("new", 2, None),
        ("load-projection", base, False, None, None, 0),
        ("read", 0),
        ("load-projection", base, False, None, None, 0),
        ("index",),
        ("ordinal-of", base.unit_id),
    ]
    for name, projection, ordinal, address, length in _projection_refusals():
        yield f"projection-{name}", [
            ("new", 2, None),
            ("load-projection", projection, False, ordinal, address, length),
            ("read", 0),
        ]
    yield "held-key", [
        ("new", 2, _keys(hashlib.sha256(b"other").hexdigest(), None)),
        ("load-projection", base, False, None, None, 0),
    ]
    yield "held-same-key", [
        ("new", 2, _keys(STORE_INPUT_KEY, None)),
        ("load-projection", base, False, None, None, 0),
        ("read", 0),
    ]
    cached = _cached_record()
    walked = unit_cache.PriorFragment(
        "units/a-class.json", 99, 41, cached.prior_id, cached.content_key, byte_copied=False, inflated=0
    )
    flagged = _cached_record(
        key=hashlib.sha256(b"flags").hexdigest(),
        slim=True,
        ink_identical=True,
        exemplar=True,
        no_verdict=True,
        duplicate_group=None,
        policy_file=None,
        pair_codepoints=None,
        mismatches=[],
        primary_units=[["", False]],
        address=[RECORDS_PART, 7, 30],
    )
    patched = (("a-class", "e-f8P2bEFApF9", False, False), ("a-class", None, True, True))
    yield "cached", [
        ("new", 3, None),
        ("load-cached", 0, cached, (0xE650, 0xE670), None),
        (
            "load-cached",
            1,
            dataclasses.replace(cached, key=hashlib.sha256(b"walked").hexdigest()),
            (0xE650,),
            walked,
        ),
        ("load-cached", 2, flagged, (0xE650, 0xE670, 0xE651), None),
        *(("read", ordinal) for ordinal in range(3)),
        ("set-primary-units", 0, [(2, True)]),
        *(("patched", ordinal, *case) for ordinal in range(3) for case in patched),
        ("index",),
    ]
    named = ["x_min", "x_max", "advance_total"]
    cached_refusals: list[tuple[str, unit_cache.ParsedCachedUnit, unit_cache.PriorFragment | None]] = [
        ("unlocated", _cached_record(address=None), None),
        ("other-unit", cached, dataclasses.replace(walked, unit_id="u-11111111111")),
        ("no-stamp", cached, dataclasses.replace(walked, content_key=None)),
        ("other-stamp", cached, dataclasses.replace(walked, content_key=STORE_INPUT_KEY)),
        ("units-count", _cached_record(primary_units=[]), None),
        ("no-before", _cached_record(junctions=[{"pair": [1, 2], "after": dict(STORE_EDGE)}]), None),
        (
            "edge-list",
            _cached_record(junctions=[{"pair": [1, 2], "before": named, "after": dict(STORE_EDGE)}]),
            None,
        ),
        ("pair-wide", _cached_record(pair_codepoints=[0, 200]), None),
        ("inflated-wide", _cached_record(address=[RECORDS_PART, 1, 5064, 2**32]), None),
    ]
    for name, record, found in cached_refusals:
        yield f"cached-{name}", [
            ("new", 2, None),
            ("load-cached", 0, record, (0xE650, 0xE670), found),
            ("read", 0),
        ]
    asked = (("a-class", None, False, False), ("b-class", None, False, False), ("a-class", None, False, True))
    yield "prior-records", [
        ("new", 2, None),
        ("load-projection", base, False, None, None, 0),
        ("add-prior-record", _prior_record("zz", address=None)),
        (
            "add-prior-record",
            _prior_record("yy", class_id="b-class", duplicate_group="e-group", exemplar=True),
        ),
        ("add-prior-record", _prior_record("store-load")),
        (
            "add-prior-record",
            _prior_record("xx", address=("units/b-class.json", 0, 7), no_verdict=True, inflated=0),
        ),
        ("add-prior-record", _prior_record("ww", primary_units=0)),
        ("seal",),
        ("seal",),
        ("prior-record-count",),
        ("read", 0),
        *(("patched", 0, *case) for case in asked),
        ("load-projection", one, False, 1, None, 0),
        ("read", 1),
        ("patched", 1, "a-class", None, False, False),
        ("set-primary-units", 0, [(1, False)]),
        ("patched", 0, "a-class", None, False, False),
    ]
    yield "prior-record-refusals", [
        ("new", 1, None),
        ("add-prior-record", _prior_record("zz", unit_id="e-11111111111")),
        ("prior-record-count",),
        ("add-prior-record", _prior_record("zz", address=("units/a.json", 2**32, 1))),
        ("prior-record-count",),
    ]
    twin = dataclasses.replace(base, input_key=hashlib.sha256(b"twin").hexdigest())
    yield "index-repeat", [
        ("new", 2, None),
        ("load-projection", base, False, None, None, 0),
        ("load-projection", twin, False, 1, None, 0),
        ("index",),
        ("ordinal-of", base.unit_id),
        ("set-primary-units", 0, [(base.unit_id, False)]),
    ]
    yield "short-keys", [("new", 2, bytes(10))]
    yield "repeated-keys", [("new", 2, _keys(STORE_INPUT_KEY, STORE_INPUT_KEY)), ("input-key-index",)]
    yield "unloaded", [
        ("new", 1, None),
        ("read", 0),
        ("windows",),
        ("primary-unit-ids",),
        ("prior-record-count",),
        ("input-key-index",),
        ("emptied",),
        ("set-input-key", 0, STORE_INPUT_KEY),
        ("read", 0),
    ]


def _store_load_generated(_source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `store-load` stage's generated lines: each store the build made, replayed and continued (`_store_continued`), then the generated stores (`_store_cases`). A generated store's read of a row is left out after a refused load, which leaves the row partly written, as Python does, where its side columns no longer line up."""
    with _UNIT_STORE_LOCK:
        runs = list(_UNIT_STORE_RUNS)
        _UNIT_STORE_RUNS.clear()
    for trace in runs:
        yield from _store_continued(trace)
    for name, calls in _store_cases():
        store: UnitStore | None = None
        refused = False
        index = 0
        for call in calls:
            if call[0] == "read" and refused:
                continue
            value, store = _unit_store_run(store, call)
            if call[0].startswith("load-"):
                refused = value[0] == "raised"  # pyright: ignore[reportIndexIssue]
            yield _unit_store_line(f"generated:store:{name}", index, call, value)
            index += 1


SCAN_STAGE = "scan"
# The `scan` stage's directory, beside its stage file, of the files its generated lines name by sha256.
SCAN_BLOBS = "scan.blobs"
# The keys of the `scan` stage's lines beside its units and their column.
SCAN_OPEN_KEY = "open"
SCAN_KEY = "scan"
SCAN_MISS_NOTE_KEY = "miss-note"
SCAN_LOCATE_KEY = "locate"
SCAN_MARKED_KEY = "marked"
SCAN_REDERIVING_KEY = "rederiving"
SCAN_ADOPT_KEY = "adopt"
# The plan's reads of the prior store as `unit_cache` defines them before any build wraps them, which the `scan` recorders and generated lines run themselves, and the key index's `find`, which a recorder's walk asks through `_FoundKeys`, so the `columns` stage follows only the build's own calls.
_OPEN_STORE = unit_cache.open_store
_SCAN_STORE = unit_cache.scan_store
_SCAN_MARKED = unit_cache.scan_marked
_LOCATE_PRIOR_FRAGMENTS = unit_cache.locate_prior_fragments
_TRUSTED_PARTS = unit_cache._trusted_parts
_MANIFEST_STAMP = unit_cache._manifest_stamp
_STORE_MISS_NOTE = unit_cache.store_miss_note
_REDERIVING_MOVED = unit_cache.rederiving_moved
_ADOPT_SINGLE_FILE = unit_cache.adopt_single_file
_KEY_INDEX_FIND = columns.KeyIndex.find
_SCAN_STORE_SIGNATURE = inspect.signature(unit_cache.scan_store)
_SCAN_MARKED_SIGNATURE = inspect.signature(unit_cache.scan_marked)
_LOCATE_SIGNATURE = inspect.signature(unit_cache.locate_prior_fragments)
_FIND_SIGNATURE = inspect.signature(unit_cache.LocatedFragments.find)


class _FoundKeys:
    """The build's input-key index as a recorder's walk asks it: through `columns.KeyIndex.find` as the module defines it, so the walk's lookups are not calls the `columns` stage follows."""

    def __init__(self, named: columns.KeyIndex) -> None:
        self._named = named

    def find(self, key: bytes) -> int | None:
        return _KEY_INDEX_FIND(self._named, key)


def _scanned_value(scanned: unit_cache.ScannedLine) -> tuple:
    """One scanned line as the `scan` stage records it: its place, its ordinal, its parsed record and the sha256 of its bytes."""
    return (scanned.index, scanned.ordinal, scanned.cached, hashlib.sha256(scanned.line).hexdigest())


def _scan_failure(error: BaseException) -> tuple:
    """How a read of the store ended that raised: `("raised", "unreadable")` for `unit_cache.StoreUnreadable`, which the plan answers by restarting over an emptied store, and `("raised", "failed")` for anything else, which fails the build."""
    return ("raised", "unreadable" if isinstance(error, unit_cache.StoreUnreadable) else "failed")


def _reopened(opened: unit_cache.OpenStore) -> unit_cache.OpenStore:
    """An open store again, over handles of its own opened by the names the store's were, so a recorder's walk leaves the build's handles where they are."""
    return unit_cache.OpenStore(
        opened.stamps, opened.head, [open(handle.name, "rb") for handle in opened.handles]
    )


def _opened_value(opened: unit_cache.OpenStore | None) -> tuple:
    """An opened store as the `open` line holds it: `("none",)`, or `("opened", stamps, head)` with the head's document, None for the single file."""
    if opened is None:
        return ("none",)
    return ("opened", opened.stamps, None if opened.head is None else opened.head.document())


def _located_value(wanted: tuple, located: unit_cache.LocatedFragments) -> tuple:
    """The walk's placements as the `locate` line holds them: the wanted records' classes, class indexes and id words, then the located columns, each word as `%016x` and the content keys as hex."""
    return (
        *wanted,
        tuple(f"{word:016x}" for word in located.words),
        tuple(located.class_of),
        tuple(located.part),
        tuple(located.start),
        tuple(located.length),
        tuple(located.inflated),
        bytes(located.content_keys).hex(),
        tuple(located.parts),
    )


def _wanted_value(wanted: unit_cache.WantedFragments) -> tuple:
    return (tuple(wanted.classes), tuple(wanted.class_of), tuple(f"{word:016x}" for word in wanted.words))


def _store_opened(_args: tuple, _kwargs: dict) -> Finisher:
    """A `unit_cache.open_store` call that returns, recorded in the `scan` boundary (`_opened_value`); the plan's second opening of an unchanged store records the same line."""

    def finish(opened: object) -> Boundary:
        assert opened is None or isinstance(
            opened, unit_cache.OpenStore
        ), f"open_store returned {opened!r:.80}"
        return Boundary(0, {}, [{"key": SCAN_OPEN_KEY, "value": _opened_value(opened)}])

    return finish


def _store_scanned(args: tuple, kwargs: dict) -> Finisher:
    """A `unit_cache.scan_store` call recorded as the `scan` boundary's units, walked again over handles of its own (`_reopened`) before the build reads a line: a unit a scanned line (`_scanned_value`), and the `scan` line, whether it parses named lines, the parts it trusts, and how it ended, `("done", lines, bytes)` or what it raised (`_scan_failure`)."""
    arguments = _SCAN_STORE_SIGNATURE.bind(*args, **kwargs).arguments
    opened, named, parse, trusted = (arguments[name] for name in ("opened", "named", "parse", "trusted"))

    def walk(ended: list[Mapping[str, object]]) -> Iterator[Mapping[str, object]]:
        lines = rows_bytes = 0
        ending: tuple | None = None
        try:
            found = _FoundKeys(named)
            for scanned in _SCAN_STORE(
                _reopened(opened), found, parse=parse, trusted=trusted  # pyright: ignore[reportArgumentType]
            ):
                yield {"value": _scanned_value(scanned)}
                lines += 1
                rows_bytes += len(scanned.line)
        except Exception as error:
            ending = _scan_failure(error)
        ended.append(
            {"key": SCAN_KEY, "value": (parse, tuple(sorted(trusted)), ending or ("done", lines, rows_bytes))}
        )

    return lambda _result: WalkedBoundary(("value",), walk)


def _marked_scanned(args: tuple, kwargs: dict) -> Finisher:
    """A `unit_cache.scan_marked` call recorded in the `scan` boundary, scanned again over handles of its own before the call runs: a `marked:<place>` line a line it parses, with its place and record, and the `marked` line, the places, the parts it trusts and how it ended, `("done", count)` or what it raised."""
    arguments = _SCAN_MARKED_SIGNATURE.bind(*args, **kwargs).arguments
    opened, marked, trusted = (arguments[name] for name in ("opened", "marked", "trusted"))
    lines: list[Mapping[str, object]] = []
    ending: tuple | None = None
    try:
        for index, cached in _SCAN_MARKED(_reopened(opened), marked, trusted):
            lines.append({"key": f"{SCAN_MARKED_KEY}:{index}", "value": (index, cached)})
    except Exception as error:
        ending = _scan_failure(error)
    lines.append(
        {
            "key": SCAN_MARKED_KEY,
            "value": (tuple(marked), tuple(sorted(trusted)), ending or ("done", len(lines))),
        }
    )
    return lambda _result: Boundary(0, {}, lines)


def _fragments_located(args: tuple, kwargs: dict) -> Finisher:
    """A `unit_cache.locate_prior_fragments` call recorded in the `scan` boundary with the wanted records and where the walk placed them (`_located_value`)."""
    wanted = _wanted_value(_LOCATE_SIGNATURE.bind(*args, **kwargs).arguments["wanted"])

    def finish(located: object) -> Boundary:
        assert isinstance(
            located, unit_cache.LocatedFragments
        ), f"locate_prior_fragments returned {located!r:.80}"
        return Boundary(0, {}, [{"key": SCAN_LOCATE_KEY, "value": _located_value(wanted, located)}])

    return finish


def _fragment_found(args: tuple, kwargs: dict) -> Finisher:
    """A `unit_cache.LocatedFragments.find` call recorded in the `scan` boundary under `find:` and the class and id it asks for, with the fragment it found or None."""
    arguments = _FIND_SIGNATURE.bind(*args, **kwargs).arguments
    key = f"find:{json.dumps([arguments['prior_class'], arguments['unit_id']])}"
    return lambda found: Boundary(0, {}, [{"key": key, "value": found}])


def _scan_called(key: str, value: Callable[[Any], object]) -> Recorder:
    """A one-line call recorded in the `scan` boundary under `key`, with `value` of what it gave."""

    def record(_args: tuple, _kwargs: dict) -> Finisher:
        return lambda result: Boundary(0, {}, [{"key": key, "value": value(result)}])

    return record


def _scan_files(directory: Path, blobs: Path) -> tuple:
    """A corpus directory as a generated `scan` line names it, sorted: each directory `(name, ("dir",))` and each file `(name, ("file", sha256))` with its bytes kept in `blobs`."""
    entries: list[tuple] = []
    for path in sorted(directory.rglob("*")):
        name = path.relative_to(directory).as_posix()
        if path.is_dir():
            entries.append((name, ("dir",)))
        else:
            entries.append((name, ("file", _blob_into(blobs, path.read_bytes()))))
    return tuple(entries)


def _scan_entries(directory: Path) -> tuple:
    """A directory's files after a call, as an `adopt` line holds them: each name, sorted, with the sha256 of its bytes."""
    return tuple(
        sorted(
            (path.name, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in directory.iterdir()
            if path.is_file()
        )
    )


def _scan_case(scratch: Path) -> Path:
    return Path(tempfile.mkdtemp(dir=scratch))


def _scan_corpus(prior: Path, target: Path, *, store: bool = True, index: bool = True) -> Path:
    """Copy into `target` the parts of a corpus the plan's reads look at: the manifest, the shard parts, and the unit store's and the units index's files where `store` and `index` say."""
    for path in sorted(prior.iterdir()):
        name = path.name
        if name == "units":
            shutil.copytree(path, target / name)
        elif (
            name == "manifest.json"
            or (store and name.startswith(unit_cache.STORE_NAME))
            or (index and name.startswith(unit_index.INDEX_NAME))
        ):
            shutil.copyfile(path, target / name)
    return target


def _scan_outcome(
    call: Callable[[], object], value: Callable[[Any], object] = lambda result: result
) -> object:
    """What a call gives as a generated line's value: `value` of its result, or what it raised (`_scan_failure`)."""
    try:
        result = call()
    except Exception as error:
        return _scan_failure(error)
    return value(result)


def _scan_lines_value(opened: unit_cache.OpenStore) -> tuple:
    """An open store's lines as an `open` line's value: their count and the sha256 of each `(place, line)`'s typed encoding, or what reading them raised, as a scan takes it: what `unit_cache._STORE_ERRORS` holds is the store unreadable."""
    try:
        count, digest = column_digest((place, line) for place, line in opened.lines())
    except unit_cache._STORE_ERRORS:
        return ("raised", "unreadable")
    except Exception as error:
        return _scan_failure(error)
    return ("lines", count, digest)


def _scan_with_open(directory: Path, then: Callable[[unit_cache.OpenStore], object]) -> object:
    """`then` over the store under `directory` once it opens, or the value of an opening that does not: `("none",)` or what it raised."""
    try:
        opened = _OPEN_STORE(directory)
    except Exception as error:
        return _scan_failure(error)
    if opened is None:
        return ("none",)
    try:
        return then(opened)
    finally:
        opened.close()


def _scan_open_value(directory: Path) -> object:
    return _scan_with_open(directory, lambda opened: (*_opened_value(opened), _scan_lines_value(opened)))


def _scanned_digest(scan: Iterable[unit_cache.ScannedLine]) -> tuple:
    """A scan as a generated `scan` line's value: its lines' count, the sha256 of their values (`_scanned_value`), their bytes, and how it ended."""
    digest = hashlib.sha256()
    count = rows_bytes = 0
    ending: tuple = ("done",)
    try:
        for scanned in scan:
            digest.update(column_entry(_scanned_value(scanned)))
            count += 1
            rows_bytes += len(scanned.line)
    except Exception as error:
        ending = _scan_failure(error)
    return ("scanned", count, digest.hexdigest(), rows_bytes, ending)


def _marked_digest(marked: Iterable[tuple[int, unit_cache.ParsedCachedUnit]]) -> tuple:
    digest = hashlib.sha256()
    count = 0
    ending: tuple = ("done",)
    try:
        for index, cached in marked:
            digest.update(column_entry((index, cached)))
            count += 1
    except Exception as error:
        ending = _scan_failure(error)
    return ("marked", count, digest.hexdigest(), ending)


def _gzip_lines(lines: Iterable[bytes]) -> bytes:
    return gzip.compress(b"".join(lines), compresslevel=1, mtime=0)


def _store_under_head(directory: Path, base: bytes, stamps: dict, *, rows: int = 0) -> row_delta.Head:
    """Write `base` as a store's generation 0 base under `directory`, with a head over it whose stamps are `stamps`."""
    name = row_delta.base_name(unit_cache.STORE_NAME, 0)
    (directory / name).write_bytes(base)
    part = row_delta.Part(name, len(base), hashlib.sha256(base).hexdigest(), rows, 0)
    head = row_delta.Head(unit_cache.STORE_NAME, 0, 0, stamps, part)
    row_delta.commit(directory, head)
    return head


def _key_of(line: bytes) -> bytes:
    return bytes.fromhex(line[len(unit_cache._KEY_PREFIX) : len(unit_cache._KEY_PREFIX) + 64].decode())


def _scan_store_cases(
    prior: Path, scratch: Path, blobs: Path, environment: fingerprint.EnvironmentStamp
) -> Iterator[tuple[tuple, object]]:
    """The store cases: the dump's prior store as it is, under a segment, as a single file, cut short, corrupted, with no header line and with lines of every shape the scan skips or refuses, each opened, scanned with keys it names and does not, with and without parsing and trusting its parts, and scanned again at marked places; and single files and heads that do not open."""
    materialized = (
        unit_cache.materialized_store(prior) if row_delta.read_head(prior, unit_cache.STORE_NAME) else None
    )
    header, lines = (
        (materialized[0], list(materialized[1]))
        if materialized
        else (
            {"format": unit_cache.STORE_FORMAT, "environment": environment.value},
            [],
        )
    )
    blank = (json.dumps(header) + "\n").encode()
    stores: list[Path] = []
    if materialized is not None:
        stores.append(_scan_corpus(prior, _scan_case(scratch), index=False))
        segmented = _scan_corpus(prior, _scan_case(scratch), index=False)
        head = row_delta.read_head(segmented, unit_cache.STORE_NAME)
        if head is not None and not head.segments and len(lines) >= 3:
            rows = [
                row_delta.Row(row_delta.UPSERT, (-1, "i"), lines[2]),
                row_delta.Row(row_delta.TOMBSTONE, (1, ""), lines[1]),
                row_delta.Row(row_delta.UPSERT, (2, ""), lines[0]),
                row_delta.Row(row_delta.UPSERT, (2, "i"), lines[1]),
            ]
            part = row_delta.write_segment(segmented, unit_cache.STORE_NAME, head.generation, 1, rows)
            row_delta.commit(segmented, row_delta.with_segment(head, part, head.stamps))
            stores.append(segmented)
        single = _scan_case(scratch)
        unit_cache.write_materialized_store(single, header, lines)
        stores.append(single)
    whole = _gzip_lines([blank, *lines])
    cut = _scan_case(scratch)
    _store_under_head(cut, whole[: max(20, len(whole) * 2 // 3)], header)
    stores.append(cut)
    corrupt = _scan_case(scratch)
    _store_under_head(corrupt, whole[:20] + bytes(byte ^ 0x55 for byte in whole[20:60]) + whole[60:], header)
    stores.append(corrupt)
    headless = _scan_case(scratch)
    _store_under_head(headless, gzip.compress(b"", mtime=0), header)
    stores.append(headless)
    key = "ab" * 32
    odd = _scan_case(scratch)
    _store_under_head(
        odd,
        _gzip_lines(
            [
                blank,
                b"not a record\n",
                b'{"key": "' + key.upper().encode() + b'", "id": 1}\n',
                b'{"key": "abc"}\n',
                *lines[:1],
                b'{"key": "' + key.encode() + b'", "id": 1}\n',
                b'{"key": "' + key.encode() + b'"',
            ]
        ),
        header,
    )
    stores.append(odd)
    for line in (b"[1]\n", b"not json\n", b""):
        bad = _scan_case(scratch)
        (bad / unit_cache.STORE_NAME).write_bytes(gzip.compress(line, mtime=0))
        stores.append(bad)
    garbage = _scan_case(scratch)
    (garbage / unit_cache.STORE_NAME).write_bytes(b"not gzip")
    stores.append(garbage)
    unparsed = _scan_case(scratch)
    row_delta.head_path(unparsed, unit_cache.STORE_NAME).write_text("{", encoding="ascii")
    stores.append(unparsed)
    missing = _scan_case(scratch)
    _store_under_head(missing, whole, header)
    (missing / row_delta.base_name(unit_cache.STORE_NAME, 0)).unlink()
    stores.append(missing)
    resized = _scan_case(scratch)
    _store_under_head(resized, whole, header)
    with (resized / row_delta.base_name(unit_cache.STORE_NAME, 0)).open("ab") as handle:
        handle.write(b"\0")
    stores.append(resized)
    named = [_key_of(line) for line in lines[:3] if line.startswith(unit_cache._KEY_PREFIX)]
    keys = (*named, bytes.fromhex(key), b"\x11" * 32)
    parts = header.get("parts")
    recorded = tuple(sorted(parts)) if isinstance(parts, dict) else ()
    column = bytearray(b"".join(keys))
    index = columns.KeyIndex(column, audit.INPUT_KEY_BYTES, len(keys))
    for store in stores:
        files = _scan_files(store, blobs)
        yield ("open", files), _scan_open_value(store)
        for parse, trusted in ((True, recorded), (True, ()), (False, recorded)):
            yield ("scan", files, keys, parse, trusted), _scan_with_open(
                store,
                lambda opened, parse=parse, trusted=trusted: _scanned_digest(
                    _SCAN_STORE(opened, index, parse=parse, trusted=frozenset(trusted))
                ),
            )
        for marked in ((0, 2, 1_000_000), (1, 0), ()):
            yield ("marked", files, marked, recorded), _scan_with_open(
                store,
                lambda opened, marked=marked: _marked_digest(
                    _SCAN_MARKED(opened, array("I", marked), frozenset(recorded))
                ),
            )


def _scan_stamp_cases(
    prior: Path, scratch: Path, blobs: Path, environment: fingerprint.EnvironmentStamp
) -> Iterator[tuple[tuple, object]]:
    """The stamp cases: the parts a store's stamps trust, under recorded sizes that hold, differ, name a missing part or are not a dict; the manifest's identity over manifests that are missing, do not parse or hold asset components; the unit store's miss note over no store, heads and single files that do not read, stamps that match and stamps that moved in each way a note names; whether a re-deriving label moved; and the single file's adoption."""
    corpus = _scan_corpus(prior, _scan_case(scratch), store=False, index=False)
    files = _scan_files(corpus, blobs)
    parts = (
        [path.relative_to(corpus).as_posix() for path in sorted((corpus / "units").glob("*.json"))]
        if (corpus / "units").is_dir()
        else []
    )
    sizes = {part: (corpus / part).stat().st_size for part in parts}
    first = parts[0] if parts else "units/none.json"
    for recorded in (
        sizes,
        {**sizes, first: sizes.get(first, 0) + 1},
        {**sizes, "units/missing.json": 3},
        {first: True},
        {first: str(sizes.get(first, 0))},
        [first],
        None,
        {},
    ):
        stamps = {"parts": recorded}
        yield ("trusted", files, stamps), _scan_outcome(
            lambda stamps=stamps: _TRUSTED_PARTS(corpus, stamps),
            lambda trusted: ("trusted", tuple(sorted(trusted))),
        )
    yield ("manifest", files), _scan_outcome(lambda: _MANIFEST_STAMP(corpus))
    for document in (
        b'{"inputs_fingerprint": {"static": "s", "spec": "x"}, "classes": []}',
        b'{"inputs_fingerprint": "s", "a": [1, {"b": null}]}',
        b"[1, 2]",
        b"\xef\xbb\xbf{}",
        b"{",
        None,
    ):
        manifest = _scan_case(scratch)
        if document is not None:
            (manifest / "manifest.json").write_bytes(document)
        yield ("manifest", _scan_files(manifest, blobs)), _scan_outcome(
            lambda manifest=manifest: _MANIFEST_STAMP(manifest)
        )
    identity = _MANIFEST_STAMP(corpus)
    lines = list(environment.lines)
    moved = (
        _moved_line(lines, "corpus_code")
        if any(line.startswith("corpus_code\t") for line in lines)
        else lines
    )
    detail = {label: list(values) for label, values in environment.detail}
    code = detail.get("corpus_code", [])
    moved_code = _moved_line(code, code[0].partition("\t")[0]) if code else code
    for stamps in (
        {"format": unit_cache.STORE_FORMAT, "environment": environment.value, "manifest_sha256": identity},
        {"format": unit_cache.STORE_FORMAT, "environment": environment.value, "manifest_sha256": "other"},
        {"environment": environment.value},
        {
            "environment": "moved",
            "environment_lines": moved,
            "environment_detail": {**detail, "corpus_code": moved_code},
        },
        {"environment": "moved", "environment_lines": [*lines, "added\tx"], "environment_detail": "none"},
        {"environment": "moved", "environment_lines": lines[1:]},
        {"environment": "moved"},
    ):
        headed = _scan_case(scratch)
        if (corpus / "manifest.json").is_file():
            shutil.copyfile(corpus / "manifest.json", headed / "manifest.json")
        _store_under_head(headed, _gzip_lines([(json.dumps(stamps) + "\n").encode()]), stamps)
        yield ("miss-note", _scan_files(headed, blobs), _stamp_fields(environment)), _scan_outcome(
            lambda headed=headed: _STORE_MISS_NOTE(headed, environment)
        )
        single = _scan_case(scratch)
        (single / unit_cache.STORE_NAME).write_bytes(_gzip_lines([(json.dumps(stamps) + "\n").encode()]))
        yield ("miss-note", _scan_files(single, blobs), _stamp_fields(environment)), _scan_outcome(
            lambda single=single: _STORE_MISS_NOTE(single, environment)
        )
        yield ("rederiving", stamps, tuple(lines)), _REDERIVING_MOVED(stamps, environment)
    for broken in ("head", "single", "none"):
        directory = _scan_case(scratch)
        if broken == "head":
            row_delta.head_path(directory, unit_cache.STORE_NAME).write_text("{", encoding="ascii")
        elif broken == "single":
            (directory / unit_cache.STORE_NAME).write_bytes(b"not gzip")
        yield ("miss-note", _scan_files(directory, blobs), _stamp_fields(environment)), _scan_outcome(
            lambda directory=directory: _STORE_MISS_NOTE(directory, environment)
        )
    for label in unit_cache.REDERIVING_LABELS:
        if any(line.startswith(f"{label}\t") for line in lines):
            stamps = {"environment_lines": _moved_line(lines, label)}
            yield ("rederiving", stamps, tuple(lines)), _REDERIVING_MOVED(stamps, environment)
    for stamps in ({"environment_lines": "not a list"}, {"environment_lines": [1, None]}):
        yield ("rederiving", stamps, tuple(lines)), _REDERIVING_MOVED(stamps, environment)
    header = {"format": unit_cache.STORE_FORMAT, "environment": environment.value}
    records = (b'{"key": "x"}\n', b"second\n")
    for leftover in (False, True):
        adopted = _scan_case(scratch)
        unit_cache.write_materialized_store(adopted, header, records)
        if leftover:
            (adopted / row_delta.base_name(unit_cache.STORE_NAME, 3)).write_bytes(b"left over")
            (adopted / row_delta.base_name(unit_cache.STORE_NAME, 0)).write_bytes(b"in the way")
        inputs = ("adopt", _scan_files(adopted, blobs), header, len(records), sum(map(len, records)))
        yield inputs, _scan_outcome(
            lambda adopted=adopted: _ADOPT_SINGLE_FILE(adopted, header, len(records), sum(map(len, records))),
            lambda head, adopted=adopted: (head.document(), _scan_entries(adopted)),
        )


def _fragment_text(fragment: Mapping[str, object]) -> bytes:
    return json.dumps([fragment], indent=1, ensure_ascii=True)[1:-2].encode()


def _deflated_corpus(
    directory: Path, *, inflated_off: int = 0, bad_key: bool = False
) -> list[tuple[str, int]]:
    """A corpus of one class whose unit store describes it: two plain fragments in its base part and one deflated in its overflow part, addressed by the store's records, the deflated one's inflated length off by `inflated_off` and, with `bad_key`, the second fragment's content key not in hex; return each fragment's class and id word."""
    class_id = "c-one"
    base, overflow = unit_index.base_part_names(class_id, 1)[0], unit_index.overflow_part_name(class_id, 0)
    fragments = []
    for number in range(3):
        content_key = hashlib.sha256(f"fragment {number}".encode()).hexdigest()
        unit_id = unit_cache.unit_id_for(content_key)
        shown = "not hex" if bad_key and number == 1 else content_key
        fragments.append({"id": unit_id, "class": class_id, "content_key": shown, "note": "·Pea"})
    texts = [_fragment_text(fragment) for fragment in fragments]
    plain = b"[" + texts[0] + b"," + texts[1] + unit_index.PART_CLOSING
    deflated = unit_index.deflate_fragment(texts[2])
    (directory / "units").mkdir()
    (directory / base).write_bytes(plain)
    (directory / overflow).write_bytes(b"[" + deflated + unit_index.PART_CLOSING)
    (directory / "manifest.json").write_text(
        json.dumps({"classes": [{"id": class_id, "shards": [base, overflow]}]}), encoding="ascii"
    )
    addresses = [
        [base, 1, len(texts[0])],
        [base, 2 + len(texts[0]), len(texts[1])],
        [overflow, 1, len(deflated), len(texts[2]) + inflated_off],
    ]
    lines = []
    for number, (fragment, address) in enumerate(zip(fragments, addresses)):
        record = {
            **_synthetic_record(),
            "key": hashlib.sha256(f"key {number}".encode()).hexdigest(),
            "id": fragment["id"],
            "class": class_id,
            "content_key": fragment["content_key"],
            "address": address,
        }
        lines.append(_record_bytes(record))
    unit_cache.write_store(directory, "e" * 64, lines, parts=[base, overflow])
    return [(class_id, unit_cache.id_word(str(fragment["id"])) or 0) for fragment in fragments]


def _wanted_from_store(corpus: Path) -> list[tuple[str, int]]:
    """Each record of the corpus's store, by class and id word, as the scan would want it placed, beside a word no fragment has and a class no manifest names."""
    materialized = unit_cache.materialized_store(corpus)
    if materialized is None:
        return []
    wanted = []
    for line in materialized[1]:
        word = unit_cache.id_word(unit_index.record_id(line))
        if word is not None:
            wanted.append((unit_index.record_class(line), word))
    if wanted:
        wanted += [(wanted[0][0], 0), (wanted[0][0], (1 << 64) - 1), ("no-such-class", wanted[0][1])]
    return wanted


def _scan_locate_cases(prior: Path, scratch: Path, blobs: Path) -> Iterator[tuple[tuple, object]]:
    """The walk's cases: the dump's prior corpus with every store record wanted, placed by its store, its index or its parts as each is taken away, by a store or an index that does not read, past a part that is not ASCII and over a fragment whose content key is not in hex; and a corpus whose deflated fragment only its store places, with that fragment's inflated length off and a content key that is not in hex."""
    wanted = _wanted_from_store(prior)
    corpora: list[tuple[Path, list[tuple[str, int]]]] = []
    if wanted:
        corpora.append((_scan_corpus(prior, _scan_case(scratch)), wanted))
        corpora.append((_scan_corpus(prior, _scan_case(scratch), store=False), wanted))
        corpora.append((_scan_corpus(prior, _scan_case(scratch), store=False, index=False), wanted))
        unread_head = _scan_corpus(prior, _scan_case(scratch), store=False)
        row_delta.head_path(unread_head, unit_cache.STORE_NAME).write_text("{", encoding="ascii")
        corpora.append((unread_head, wanted))
        refused = _scan_corpus(prior, _scan_case(scratch), store=False, index=False)
        row_delta.head_path(refused, unit_cache.STORE_NAME).write_text("{", encoding="ascii")
        corpora.append((refused, wanted))
        unread_index = _scan_corpus(prior, _scan_case(scratch), store=False)
        row_delta.head_path(unread_index, unit_index.INDEX_NAME).write_text("{", encoding="ascii")
        corpora.append((unread_index, wanted))
        no_manifest = _scan_corpus(prior, _scan_case(scratch))
        (no_manifest / "manifest.json").unlink()
        corpora.append((no_manifest, wanted))
        shards = sorted((prior / "units").glob("*.json"))
        if shards:
            not_ascii = _scan_corpus(prior, _scan_case(scratch), store=False)
            with (not_ascii / "units" / shards[0].name).open("ab") as handle:
                handle.write(b"\x80")
            corpora.append((not_ascii, wanted))
            for raw_text in (shard.read_bytes() for shard in shards):
                found = re.search(rb'"content_key": "([0-9a-f]{64})"', raw_text)
                if found and raw_text.isascii():
                    upper = _scan_corpus(prior, _scan_case(scratch), store=False, index=False)
                    for shard in shards:
                        data = shard.read_bytes()
                        (upper / "units" / shard.name).write_bytes(
                            data.replace(found.group(1), found.group(1).upper(), 1)
                        )
                    corpora.append((upper, wanted))
                    break
    for inflated_off, bad_key in ((0, False), (1, False), (0, True)):
        synthetic = _scan_case(scratch)
        corpora.append((synthetic, _deflated_corpus(synthetic, inflated_off=inflated_off, bad_key=bad_key)))
        stripped = _scan_case(scratch)
        stripped_wanted = _deflated_corpus(stripped, inflated_off=inflated_off, bad_key=bad_key)
        for name in _store_files(stripped):
            (stripped / name).unlink()
        corpora.append((stripped, stripped_wanted))
    loose = _scan_case(scratch)
    (loose / "units").mkdir()
    content_key = hashlib.sha256(b"loose").hexdigest()
    unit_id = unit_cache.unit_id_for(content_key)
    (loose / "units" / "c-two.json").write_text(
        json.dumps(
            [
                1,
                {"id": 5},
                {"id": unit_id, "content_key": content_key},
                {"id": unit_id, "content_key": content_key},
            ]
        ),
        encoding="ascii",
    )
    (loose / "manifest.json").write_text(
        json.dumps(
            {"classes": [{"id": "c-two", "shard": "units/c-two.json"}, {"id": "c-three", "shards": []}]}
        ),
        encoding="ascii",
    )
    corpora.append((loose, [("c-two", unit_cache.id_word(unit_id) or 0), ("c-three", 1)]))
    for corpus, entries in corpora:
        holder = unit_cache.WantedFragments()
        for class_id, word in entries:
            holder.add(class_id, word)
        inputs = ("locate", _scan_files(corpus, blobs), *_wanted_value(holder))
        yield inputs, _scan_outcome(
            lambda corpus=corpus, holder=holder: _LOCATE_PRIOR_FRAGMENTS(corpus, holder),
            lambda located, holder=holder: _located_value(_wanted_value(holder), located),
        )


def _scan_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `scan` stage's generated lines, over the dump's prior corpus and corpora made from it and by hand (`_scan_store_cases`, `_scan_stamp_cases`, `_scan_locate_cases`), each keyed by a digest of its input, whose files are kept once under their sha256 in `scan.blobs/` beside the stage file (`SCAN_BLOBS`). A live dump has none, since each case copies the prior corpus and keeps its files."""
    if source.scenario.source != "mini":
        return
    document = json.loads((source.out / BUILD_INPUT_FILE).read_text(encoding="ascii"))
    stamp = document["environment"]
    environment = fingerprint.EnvironmentStamp(
        tuple(stamp["lines"]), tuple((label, tuple(lines)) for label, lines in stamp["detail"])
    )
    prior = source.out / DUMP_PRIOR
    blobs = source.out / SCAN_BLOBS
    with tempfile.TemporaryDirectory(prefix="scan-", dir=source.out) as held:
        scratch = Path(held)
        for inputs, value in chain(
            _scan_store_cases(prior, scratch, blobs, environment),
            _scan_stamp_cases(prior, scratch, blobs, environment),
            _scan_locate_cases(prior, scratch, blobs),
        ):
            key = hashlib.sha1(compact(encode(inputs)).encode("ascii")).hexdigest()[:20]
            yield {"key": f"generated:{key}", "input": inputs, "value": value}


PLAN_STAGE = "plan"
# The labels of the load's and the plan's `[t]` lines, which `build._build_m1` passes `build._phase_timing`.
_LOAD_PHASE = "review.build load"
_PLAN_PHASE = "review.build plan"
# The keys of the `plan` stage's lines beside its units and their column: the column of the prior records the store keeps by id, the recomputed ordinals' column, the store plan, the prior shards, the count reused, the verification sample, the plan's note and the console lines the plan printed.
PLAN_PRIOR_RECORDS = "prior-records"
PLAN_RECOMPUTED = "recomputed"
PLAN_STORE_PLAN_KEY = "store-plan"
PLAN_PRIOR_SHARDS_KEY = "prior-shards"
PLAN_CACHED_KEY = "cached"
PLAN_SAMPLE_KEY = "sample"
PLAN_NOTE_KEY = "note"
PLAN_CONSOLE_KEY = "console"
_PHASE_TIMING = inspect.signature(build._phase_timing)


def _unit_value(unit: Unit) -> Record:
    """A materialized unit as the build stages hold it: a record of `audit.Unit`'s fields in their order but `ink_deltas`, its config classes as a dict in their order."""
    return Record(
        "Unit",
        tuple(
            (item.name, dict(value.items()) if item.name == "config_classes" else value)
            for item in dataclasses.fields(unit)
            if item.name != "ink_deltas"
            for value in (getattr(unit, item.name),)
        ),
    )


def plan_boundary(held: Mapping[str, Any], note: str, printed: Sequence[str]) -> Boundary:
    """The `plan` boundary over `build._build_m1`'s locals as the plan's line is printed (`held`): a unit a row of the unit store as the `store-load` stage reads one (`_store_reading`); a column over the prior records the store keeps by id (`_sealed_records`) and one over the recomputed ordinals; the store plan, its head as its document; the prior shards, each dict as its sorted items, or None; the count of units reused; the verification sample, its ordinals and the units materialized for it (`_unit_value`); the plan's note; and the console lines `printed` while the plan ran, its `[t]` line not among them."""
    store: UnitStore = held["store"]
    store_plan: unit_cache.StorePlan = held["store_plan"]
    prior_shards: build.PriorShards | None = held["prior_shards"]
    return Boundary(
        store.n,
        {"value": functools.partial(_store_reading, store)},
        [
            column_line(PLAN_PRIOR_RECORDS, _sealed_records(store)),
            column_line(PLAN_RECOMPUTED, held["recomputed"]),
            {
                "key": PLAN_STORE_PLAN_KEY,
                "value": (
                    store_plan.existed,
                    store_plan.readable,
                    None if store_plan.head is None else store_plan.head.document(),
                    store_plan.lines,
                    store_plan.rows_bytes,
                    store_plan.rederived,
                ),
            },
            {
                "key": PLAN_PRIOR_SHARDS_KEY,
                "value": (
                    None
                    if prior_shards is None
                    else (tuple(sorted(prior_shards.sizes.items())), tuple(sorted(prior_shards.live.items())))
                ),
            },
            {"key": PLAN_CACHED_KEY, "value": held["cached_count"]},
            {
                "key": PLAN_SAMPLE_KEY,
                "value": (tuple(held["sampled_ordinals"]), tuple(map(_unit_value, held["verify_units"]))),
            },
            {"key": PLAN_NOTE_KEY, "value": note},
            {"key": PLAN_CONSOLE_KEY, "value": list(printed)},
        ],
    )


# The console the plan prints to while the `plan` stage records it: standard error held from the end of the load's line to the start of the plan's.
_plan_console: list[_Printed] = []


def _build_locals() -> Mapping[str, Any]:
    """The locals of the `build._build_m1` frame the hooked call was made from."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_code is not build._build_m1.__code__:
        frame = frame.f_back
    assert frame is not None, "the hooked call is made from build._build_m1"
    return frame.f_locals


def _plan_timed(args: tuple, kwargs: dict) -> Finisher | None:
    """A `build._phase_timing` call recorded for the `plan` boundary. Once the load's line is printed, standard error is held (`_Printed`); the plan's line, before it prints, ends the hold and records the boundary over `build._build_m1`'s locals (`plan_boundary`) with the note and the lines held. Every other phase's line records nothing."""
    arguments = _PHASE_TIMING.bind(*args, **kwargs).arguments
    if arguments["label"] == _LOAD_PHASE:

        def hold(_result: object) -> None:
            printed = _Printed(sys.stderr)
            sys.stderr = printed
            _plan_console[:] = [printed]

        return hold
    if arguments["label"] != _PLAN_PHASE:
        return None
    (printed,) = _plan_console
    _plan_console.clear()
    sys.stderr = printed.through
    boundary = plan_boundary(_build_locals(), arguments["note"], printed.lines())
    return lambda _result: boundary


UNITS_STAGE = "units"
# The keys of the `units` stage's lines beside its units: the kept errors, the `[progress]` lines, the other console lines and the arena's counts.
UNITS_ERRORS_KEY = "errors"
UNITS_PROGRESS_KEY = "progress"
UNITS_CONSOLE_KEY = "console"
UNITS_ARENA_KEY = "arena"


def _units_reading(store: UnitStore, arena: build._FragmentArena, ordinal: int) -> dict[str, object]:
    """One row of the store as the `units` stage reads it: the row as the `store-load` stage reads one (`_store_reading`), a source in the arena as its part, its length and the sha256 of its deflated bytes, since where a fragment lies in the arena follows the order the units engine answered in."""
    reading = _store_reading(store, ordinal)
    source = store.source(ordinal)
    if source is not None and source.part == build.ARENA_PART:
        packed = bytes(arena._bytes[source.start : source.start + source.length])
        reading["source"] = (source.part, source.length, hashlib.sha256(packed).hexdigest())
    return reading


def units_boundary(runner: build._RecomputeRunner, store: UnitStore, printed: Sequence[str]) -> Boundary:
    """The `units` boundary over the unit store once phase 1 has loaded every recomputed unit (`_RecomputeRunner.phase1`): a unit a row as `_units_reading` reads it; the drafting-time errors the runner kept, sorted, since a pooled build keeps them in arrival order; how many `[progress]` lines phase 1 printed and the last of them; the other lines it printed; and the arena's fragment count, deflated bytes, shard bytes and note."""
    arena = runner.arena
    progress = [line for line in printed if line.startswith(console.PROGRESS)]
    return Boundary(
        store.n,
        {"value": functools.partial(_units_reading, store, arena)},
        [
            {"key": UNITS_ERRORS_KEY, "value": sorted(runner.contract_errors)},
            {"key": UNITS_PROGRESS_KEY, "value": (len(progress), progress[-1] if progress else None)},
            {
                "key": UNITS_CONSOLE_KEY,
                "value": [line for line in printed if not line.startswith(console.PROGRESS)],
            },
            {"key": UNITS_ARENA_KEY, "value": (arena.count, len(arena), arena._raw, arena.note())},
        ],
    )


def _units_drafted(args: tuple, _kwargs: dict) -> Finisher:
    """Phase 1 (`build._RecomputeRunner.phase1`) recorded as the `units` boundary once it returns (`units_boundary`), with the console lines it printed, read from standard error, which is held for the length of the call."""
    runner, store = args
    printed = _Printed(sys.stderr)
    sys.stderr = printed

    def finish(_result: object) -> Boundary:
        sys.stderr = printed.through
        return units_boundary(runner, store, printed.lines())

    return finish


TRIAGE_STAGE = "triage"
# The keys of the `triage` stage's lines beside its units: the classes in manifest order, each class's section, the triage order's column and the batch count.
TRIAGE_CLASSES_KEY = "classes"
TRIAGE_SECTIONS_KEY = "sections"
TRIAGE_ORDER = "order"
TRIAGE_BATCHES_KEY = "batches"


def _triage_reading(table: UnitTable, ordinal: int) -> tuple[str, str, bool, int | None, int | None]:
    """One unit as the `triage` stage reads it from the table: its final class, its unmatched group, its exemplar flag, and its place in the triage index and its batch, each None for a unit outside the index."""
    return (
        table.class_id(ordinal),
        table.unmatched_group(ordinal),
        table.exemplar(ordinal),
        table.order(ordinal),
        table.batch(ordinal),
    )


def triage_boundary(held: Mapping[str, Any], batches: object) -> Boundary:
    """The `triage` boundary over `build._build_m1`'s locals once `assign_batches` has numbered the human units (`held`): a unit as `_triage_reading` reads it; the classes in manifest order; each class's section, in the order the build met the classes before sorting them; a column over the triage order; and the batch count."""
    table: UnitTable = held["table"]
    return Boundary(
        table.n,
        {"value": functools.partial(_triage_reading, table)},
        [
            {"key": TRIAGE_CLASSES_KEY, "value": list(held["classes"])},
            {"key": TRIAGE_SECTIONS_KEY, "value": list(held["sections"].items())},
            column_line(TRIAGE_ORDER, held["order"]),
            {"key": TRIAGE_BATCHES_KEY, "value": batches},
        ],
    )


def _batches_assigned(_args: tuple, _kwargs: dict) -> Finisher:
    """`audit.assign_batches` as `build._build_m1` calls it, recorded as the `triage` boundary once it returns the batch count (`triage_boundary`)."""
    return lambda batches: triage_boundary(_build_locals(), batches)


PASSES_STAGE = "passes"
# The keys of the `passes` stage's lines beside its units: the duplicate-group count, the classes' rows, the secondary-junction counts, the verification's keys, the mismatches and the units phase's note.
PASSES_DUPLICATE_GROUPS_KEY = "duplicate_groups"
PASSES_BY_CLASS = "by_class"
PASSES_JUNCTIONS_KEY = "secondary_junctions"
PASSES_VERIFIED_KEY = "verified"
PASSES_MISMATCHES_KEY = "mismatches"
PASSES_NOTE_KEY = "note"
_UNITS_PHASE = "review.build units"


def _passes_reading(
    table: UnitTable, store: UnitStore, ordinal: int
) -> tuple[str | None, str | None, tuple[tuple[str | None, bool], ...]]:
    """One unit as the `passes` stage reads it: its duplicate group and cluster from the table, and its primary units from the store, a `(primary unit, suppressed)` pair a secondary junction."""
    return table.duplicate_group(ordinal), table.cluster(ordinal), store.primary_units(ordinal)


def passes_boundary(held: Mapping[str, Any], note: str) -> Boundary:
    """The `passes` boundary over `build._build_m1`'s locals as the units phase's line is printed, once the stale check has passed (`held`): a unit as `_passes_reading` reads it; the duplicate-group count; a column over the classes' rows, a `(class, ordinals)` pair a class in the order they first appear along the triage order; the secondary-junction counts as their items; the verification's keys, each `(id, content key, ink deltas)`, sorted, since the order the recomputations answer in reaches no output; the mismatches; and the units phase's note."""
    table: UnitTable = held["table"]
    store: UnitStore = held["store"]
    verified: Mapping[str, tuple[str, Sequence[Sequence[str]]]] = held["verified"]
    return Boundary(
        table.n,
        {"value": functools.partial(_passes_reading, table, store)},
        [
            {"key": PASSES_DUPLICATE_GROUPS_KEY, "value": held["duplicate_group_count"]},
            column_line(
                PASSES_BY_CLASS,
                ((class_id, tuple(members)) for class_id, members in held["by_class"].items()),
            ),
            {"key": PASSES_JUNCTIONS_KEY, "value": list(held["secondary_junction_counts"].items())},
            {
                "key": PASSES_VERIFIED_KEY,
                "value": sorted(
                    (unit_id, key, tuple(tuple(pair) for pair in deltas))
                    for unit_id, (key, deltas) in verified.items()
                ),
            },
            {"key": PASSES_MISMATCHES_KEY, "value": list(held["mismatches"])},
            {"key": PASSES_NOTE_KEY, "value": note},
        ],
    )


def _passes_timed(args: tuple, kwargs: dict) -> Finisher | None:
    """A `build._phase_timing` call recorded for the `passes` boundary: the units phase's line, which `build._build_m1` prints once the stale check has passed, records the boundary over its locals (`passes_boundary`) with the line's note. Every other phase's line records nothing."""
    arguments = _PHASE_TIMING.bind(*args, **kwargs).arguments
    if arguments["label"] != _UNITS_PHASE:
        return None
    boundary = passes_boundary(_build_locals(), arguments["note"])
    return lambda _result: boundary


def _passes_generated(_source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `passes` stage's generated lines: duplicate-group keys as `build._build_m1` builds them, over one configuration and several, as the table's tuple and as a list, with a judged pair and with None, an empty class and digest, and a class and digest that `repr` must quote and escape, each under `generated:duplicate-group:<n>` with the id `unit_cache.duplicate_group_id_for` gives its repr."""
    one = (ACCEPTANCE_CONFIGS[0],)
    several = tuple(ACCEPTANCE_CONFIGS)
    keys: list[tuple[object, ...]] = [
        (one, None, audit.UNMATCHED_CLASS, ""),
        (one, (0xE650, 0xE67A), "tea-utter", "0f" * 32),
        (several, None, "per-unit-class", "ab" * 32),
        (several, (0xE666, 0xE652), "class", "digest"),
        (list(several), (0xE652, 0xE67A), "class", "digest"),
        ((), None, "", ""),
        (one, (1, 2), '·Tea ~b~ ·Utter\'s "class"', "back\\slash\ttab\x7fé\U0001f600"),
    ]
    for index, key in enumerate(keys):
        yield {
            "key": f"generated:duplicate-group:{index}",
            "input": key,
            "value": unit_cache.duplicate_group_id_for(repr(key)),
        }


ROUTE_STAGE = "route"
# The keys of the `route` stage's lines beside its units: a line a class in shard order under its place, the classes' ordinals, the runs and the copy-forward decision.
ROUTE_CLASS_PREFIX = "class:"
ROUTE_ORDINALS = "class-ordinals"
ROUTE_RUNS_KEY = "runs"
ROUTE_COPY_FORWARD_KEY = "copy-forward"
_WRITE_RUNS = inspect.signature(build._write_runs)


def _write_locals() -> Mapping[str, Any]:
    """The locals of the `build._write_corpus` frame the hooked call was made from."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_code is not build._write_corpus.__code__:
        frame = frame.f_back
    assert frame is not None, "the hooked call is made from build._write_corpus"
    return frame.f_locals


def route_boundary(held: Mapping[str, Any], width: int, runs: object) -> Boundary:
    """The `route` boundary over `build._write_corpus`'s locals once `_write_runs` has cut the runs (`held`): a unit whether `_route` predicted its fragment unchanged; a line a class in shard order, under its place there, with its id, route, prior parts and manifest record (`build._class_meta`, which the write computes from the same columns as the class's first chunk is made); a column over each class's id and ordinals in shard order; the width the runs were cut at and each run as its classes' places; and whether the previous sidecars are copied forward."""
    table: UnitTable = held["table"]
    store: UnitStore = held["store"]
    to_write: Sequence[build._ClassToWrite] = held["to_write"]
    predicted: bytearray = held["predicted"]
    assert isinstance(runs, list), runs
    places = {id(entry): place for place, entry in enumerate(to_write)}
    return Boundary(
        table.n,
        {"value": lambda ordinal: bool(predicted[ordinal])},
        [
            *(
                {
                    "key": f"{ROUTE_CLASS_PREFIX}{place}",
                    "value": (
                        entry.id,
                        entry.route,
                        entry.prior_parts,
                        build._class_meta(entry, table, store),
                    ),
                }
                for place, entry in enumerate(to_write)
            ),
            column_line(ROUTE_ORDINALS, ((entry.id, tuple(entry.ordinals)) for entry in to_write)),
            {"key": ROUTE_RUNS_KEY, "value": (width, [[places[id(entry)] for entry in run] for run in runs])},
            {"key": ROUTE_COPY_FORWARD_KEY, "value": held["copy_forward"]},
        ],
    )


def _runs_cut(args: tuple, kwargs: dict) -> Finisher:
    """`build._write_runs` as `build._write_corpus` calls it, recorded as the `route` boundary once it returns the runs (`route_boundary`), with the width it cut them at."""
    width = _WRITE_RUNS.bind(*args, **kwargs).arguments["width"]
    return lambda runs: route_boundary(_write_locals(), width, runs)


# The class sizes and the widths the `route` stage's generated lines cut runs over: no class, empty classes, one class, even and uneven classes, a large class first and last; and widths below one, one, a few and more than there are units.
_ROUTE_SIZES: tuple[tuple[int, ...], ...] = (
    (),
    (0,),
    (0, 0),
    (5,),
    (3, 3, 3),
    (10, 1, 1, 1, 1, 1),
    (1, 1, 1, 10),
    (4, 4, 4, 4),
    (7, 2, 9, 1, 0, 3),
)
_ROUTE_FITS = (-1, 0, 1, 2, 3, 4, 8, 100)
# The manifests the `route` stage's generated lines read the previous corpus's parts from, None for a corpus with none: both entry forms, a repeated id, ids that are not strings, parts that are not strings or a list, and manifests that do not read, do not parse or do not hold classes, each of which reads as no parts.
_ROUTE_MANIFESTS: tuple[bytes | None, ...] = (
    None,
    b'{"classes": [{"id": "a", "shards": ["units/a.000.json", "units/a.x000.json"]}, {"id": "b", "shard": "units/b.json"}]}',
    b'{"classes": [{"id": "a", "shard": 5, "shards": null}, {"id": "b", "shards": []}, {"id": "a", "shards": ["units/a.json"]}]}',
    b'{"classes": [{"id": 1, "shards": ["one"]}, {"id": true, "shards": ["true"]}, {"id": null, "shards": "ab"}]}',
    b'{"classes": [{"id": "a", "shards": [1, null, true, {"k": [2]}, "\xc2\xb7Tea"]}, {"id": "b", "shards": {"x": 1, "y": 2}}]}',
    b'{"classes": [{"id": "a", "shards": ["units/a.json"]}, {"id": ["a"], "shards": []}]}',
    b'{"classes": [{"id": "a", "shards": ["units/a.json"]}, {"id": "b"}]}',
    b'{"classes": [{"id": "a", "shards": 3}]}',
    b'{"classes": [{"id": "a", "shards": false}]}',
    b'{"classes": [["a"]]}',
    b'{"classes": [{"shards": []}]}',
    b'{"classes": "ab"}',
    b'{"classes": {}}',
    b'{"classes": null}',
    b'{"classes": []}',
    b'{"other": []}',
    b'[{"classes": []}]',
    b'{"classes": [}',
    b'{"classes": [{"id": "\xff"}]}',
    b'\xef\xbb\xbf{"classes": []}',
)


def _route_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `route` stage's generated lines: `build._write_width` and `build._write_runs` over classes of each of `_ROUTE_SIZES` at each of `_ROUTE_FITS`, each under `generated:runs:<n>` with the sizes and the width as its input and as its value the width the write would take and each run as its classes' places; `build._prior_parts` over a corpus holding each of `_ROUTE_MANIFESTS`, each under `generated:prior-parts:<n>` with the manifest's bytes as its input and the parts' items as its value; and, under `generated:current`, whether the dump's prior corpus holds a current units index (`unit_index.index_is_current`), app index and locator (`app_index.artifact_is_current`)."""
    count = 0
    for sizes in _ROUTE_SIZES:
        for fit in _ROUTE_FITS:
            classes = [
                build._ClassToWrite(
                    str(place), "", "", "", "", "", False, False, "", array("I", range(size)), ()
                )
                for place, size in enumerate(sizes)
            ]
            yield {
                "key": f"generated:runs:{count}",
                "input": (sizes, fit),
                "value": (
                    build._write_width(classes, fit),
                    [[int(entry.id) for entry in run] for run in build._write_runs(classes, fit)],
                ),
            }
            count += 1
    with tempfile.TemporaryDirectory() as directory:
        corpus = Path(directory)
        manifest = corpus / "manifest.json"
        for index, raw in enumerate(_ROUTE_MANIFESTS):
            manifest.unlink(missing_ok=True)
            if raw is not None:
                manifest.write_bytes(raw)
            yield {
                "key": f"generated:prior-parts:{index}",
                "input": raw,
                "value": list(build._prior_parts(corpus).items()),
            }
    prior = source.out / DUMP_PRIOR
    yield {
        "key": "generated:current",
        "input": None,
        "value": (
            unit_index.index_is_current(prior),
            app_index.artifact_is_current(prior, app_index.APP_INDEX_NAME, app_index.APP_INDEX_FORMAT),
            app_index.artifact_is_current(prior, app_index.LOCATOR_NAME, app_index.LOCATOR_FORMAT),
        ),
    }


SHARDS_STAGE = "shards"
# The `shards` stage's directory, beside its stage file, of the bytes its lines name by sha256: each fragment handed to a writer and each part a class it appends to held as the class opened.
SHARDS_BLOBS = "shards.blobs"
# The blob directory of the build whose `shards` stage records, for the length of that build (`_shards_blobs_at`).
_SHARDS_BLOBS_AT: list[Path] = []
_SHARD_OPEN = inspect.signature(build._ShardWriter.open)
_SHARD_HOLDS = inspect.signature(build._ShardWriter.holds)
_SHARD_KEEP = inspect.signature(build._ShardWriter.keep)
_SHARD_ADD = inspect.signature(build._ShardWriter.add_bytes)
_SHARD_CLOSE = inspect.signature(build._ShardWriter.close)


@dataclass
class _ShardClass:
    """A class one writer of the recorded build has open: the parts it appends to as the directory held them when it opened (`_shard_held`), each call made on the writer since `open` with its result, and how many renames were pending and where the writer's counters stood as it opened."""

    held: list[tuple[str, str]]
    ops: list[tuple]
    results: list[object]
    pending: int
    counters: tuple[int, int, int]


# The class each writer of the recorded build has open, by the writer's `id`.
_SHARD_CLASSES: dict[int, _ShardClass] = {}


@contextlib.contextmanager
def _shards_blobs_at(directory: Path) -> Iterator[None]:
    _SHARDS_BLOBS_AT.append(directory)
    try:
        yield
    finally:
        _SHARDS_BLOBS_AT.pop()
        _SHARD_CLASSES.clear()


def _shard_counters(writer: build._ShardWriter) -> tuple[int, int, int]:
    return writer.appended, writer.appended_inflated, writer.deflated


def _shard_counted(writer: build._ShardWriter, since: tuple[int, int, int]) -> tuple[int, ...]:
    """The writer's counters past where they stood at `since`, as a writer made for the class alone counts them."""
    return tuple(now - then for now, then in zip(_shard_counters(writer), since, strict=True))


def _shard_held(directory: Path, parts: Iterable[str], blobs: Path) -> list[tuple[str, str]]:
    """Each of `parts` that `directory` holds as a file, by its path there, with its bytes kept in `blobs` under their sha256."""
    return [
        (part, _blob_into(blobs, (directory / part).read_bytes()))
        for part in parts
        if (directory / part).is_file()
    ]


def _shard_file(directory: Path, path: Path) -> tuple[str, int, str]:
    """A file under `directory` as the `shards` stage names it: its path there, its size and its sha256."""
    data = path.read_bytes()
    return path.relative_to(directory).as_posix(), len(data), hashlib.sha256(data).hexdigest()


def _shard_pending(writer: build._ShardWriter, since: int) -> list[tuple[str, str, int, str]]:
    """The renames pending on `writer` past the first `since`, each as its staging file's path under the writer's directory, its target's, and the staging file's size and sha256."""
    directory = writer._out_dir
    pending = []
    for staging, target in writer._pending[since:]:
        name, size, digest = _shard_file(directory, staging)
        pending.append((name, target.relative_to(directory).as_posix(), size, digest))
    return pending


def _shard_entries(directory: Path) -> list[tuple[str, int, str]]:
    """Every file under `directory`, sorted by its path there (`_shard_file`)."""
    return sorted(_shard_file(directory, path) for path in directory.rglob("*") if path.is_file())


def _shard_writer_call(
    signature: inspect.Signature, args: tuple, kwargs: dict
) -> tuple[build._ShardWriter, dict[str, Any]]:
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    arguments = dict(bound.arguments)
    return arguments.pop("self"), arguments


def _shard_step(writer: build._ShardWriter, op: tuple) -> Finisher:
    def finish(result: object) -> None:
        opened = _SHARD_CLASSES[id(writer)]
        opened.ops.append(op)
        opened.results.append(tuple(result) if isinstance(result, tuple) else result)

    return finish


def _shard_opened(args: tuple, kwargs: dict) -> Finisher:
    """`build._ShardWriter.open`: the class's first call, with the parts the writer appends to held as its directory holds them now."""
    writer, arguments = _shard_writer_call(_SHARD_OPEN, args, kwargs)
    prior_parts = tuple(arguments["prior_parts"])
    held = _shard_held(writer._out_dir, prior_parts, _SHARDS_BLOBS_AT[-1]) if arguments["append"] else []
    op = ("open", arguments["class_id"], prior_parts, arguments["append"], arguments["deflate"])

    def finish(_result: object) -> None:
        _SHARD_CLASSES[id(writer)] = _ShardClass(
            held, [op], [None], len(writer._pending), _shard_counters(writer)
        )

    return finish


def _shard_holds(args: tuple, kwargs: dict) -> Finisher:
    writer, arguments = _shard_writer_call(_SHARD_HOLDS, args, kwargs)
    return _shard_step(writer, ("holds", arguments["part"]))


def _shard_keep(args: tuple, kwargs: dict) -> Finisher:
    writer, arguments = _shard_writer_call(_SHARD_KEEP, args, kwargs)
    source: unit_cache.PriorFragment = arguments["source"]
    return _shard_step(writer, ("keep", source.part, source.start, source.length, source.inflated))


def _shard_added(args: tuple, kwargs: dict) -> Finisher:
    """`build._ShardWriter.add_bytes`, which `add` makes too, with the fragment's bytes kept as a blob."""
    writer, arguments = _shard_writer_call(_SHARD_ADD, args, kwargs)
    digest = _blob_into(_SHARDS_BLOBS_AT[-1], bytes(arguments["body"]))
    return _shard_step(writer, ("add", digest, arguments["inflated"]))


def _shard_closed(args: tuple, kwargs: dict) -> Finisher:
    """`build._ShardWriter.close`, which ends the class's line: its key the class id, its input the cap (None, the build's), the parts the writer appends to as it found them and every call since `open`, and its value each call's result, the close's parts, total and the staged parts it leaves pending with their sizes and sha256, then the counters the class moved (`_shard_counted`)."""
    writer, _arguments = _shard_writer_call(_SHARD_CLOSE, args, kwargs)
    class_id = writer._class_id

    def finish(result: object) -> Mapping[str, object]:
        opened = _SHARD_CLASSES.pop(id(writer))
        assert isinstance(result, tuple), result
        names, total = result
        opened.ops.append(("close",))
        opened.results.append((list(names), total, _shard_pending(writer, opened.pending)))
        return {
            "key": f"class:{class_id}",
            "input": (None, opened.held, opened.ops),
            "value": (opened.results, _shard_counted(writer, opened.counters)),
        }

    return finish


@contextlib.contextmanager
def _shard_cap(cap: int | None) -> Iterator[None]:
    """`build.SHARD_PART_BYTES`, the cap `_ShardWriter` cuts its parts at, set to `cap` while the block runs, and left as it stands for None."""
    standing = build.SHARD_PART_BYTES
    if cap is not None:
        build.SHARD_PART_BYTES = cap
    try:
        yield
    finally:
        build.SHARD_PART_BYTES = standing


def _shard_call(writer: build._ShardWriter, directory: Path, op: tuple, blobs: Path, since: int) -> object:
    """Make one call a `shards` line names on `writer` over `directory` and return its result as the line records it. `since` is how many renames were pending as the open class opened."""
    match op:
        case ("open", str(class_id), tuple(prior_parts), bool(append), bool(deflate)):
            writer.open(class_id, prior_parts, append=append, deflate=deflate)
            return None
        case ("holds", str(part)):
            return writer.holds(part)
        case ("keep", str(part), int(start), int(length), int(inflated)):
            return tuple(
                writer.keep(unit_cache.PriorFragment(part, start, length, "", None, inflated=inflated))
            )
        case ("add", str(digest), int(inflated)):
            return tuple(writer.add_bytes((blobs / digest).read_bytes(), inflated))
        case ("close",):
            names, total = writer.close()
            return list(names), total, _shard_pending(writer, since)
        case ("take-pending",):
            return [
                (staging.relative_to(directory).as_posix(), target.relative_to(directory).as_posix())
                for staging, target in writer.take_pending()
            ]
        case ("commit",):
            writer.commit()
            return None
        case ("abort",):
            writer.abort()
            return None
        case ("entries",):
            return _shard_entries(directory)
        case ("discard", tuple(class_ids)):
            build._discard_runs(
                directory,
                [
                    [
                        build._ClassToWrite(class_id, "", "", "", "", "", False, False, "", array("I"), ())
                        for class_id in class_ids
                    ]
                ],
            )
            return None
    raise TypeError(f"no shard writer call {op!r}")


def _shard_replay(
    directory: Path, cap: int | None, held: Sequence[tuple[str, str]], ops: Sequence[tuple], blobs: Path
) -> tuple[list[object], tuple[int, int, int]]:
    """Make the files `held` names in `directory` from `blobs`, then make each call of `ops` on a new `build._ShardWriter` over it, with the parts cut at `cap` (`_shard_cap`), as the Rust side replays a line: each call's result, `("raised", class, message)` for one that raises ValueError or KeyError, and the writer's counters after the last."""
    for name, digest in held:
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((blobs / digest).read_bytes())
    with _shard_cap(cap):
        writer = build._ShardWriter(directory)
        results: list[object] = []
        since = 0
        for op in ops:
            try:
                results.append(_shard_call(writer, directory, op, blobs, since))
            except (ValueError, KeyError) as error:
                results.append(("raised", type(error).__name__, str(error)))
            if op[0] == "open":
                since = len(writer._pending)
    return results, _shard_counters(writer)


def _shard_body(length: int, seed: int) -> bytes:
    """A generated fragment's plain bytes: `length` ASCII letters, varied by `seed`."""
    return bytes(0x61 + (seed * 7 + index * index) % 26 for index in range(length))


def _shard_part(*bodies: bytes) -> bytes:
    """A shard part holding `bodies`, framed as `_ShardWriter` frames one."""
    return b"[" + b",".join(bodies) + unit_index.PART_CLOSING


def _shard_cases() -> Iterator[tuple[str, int | None, dict[str, bytes], list[tuple]]]:
    """The `shards` stage's generated writers, each its name, its cap (None for `SHARD_PART_BYTES`), the parts its directory holds and its calls, with each fragment as its bytes: base parts cut where a fragment would pass the cap, exactly at it, and past it alone; an empty class; a first base handed deflated fragments, good and refused by each of `inflate_fragment`'s checks; a fold that deflates plain fragments, an empty one among them, and keeps deflated ones as they are; appends to a last overflow part that has room, that has none, that is missing, that the parts list out of number order, that is numbered past three digits or in other decimal digits, beside a part that is not an overflow part and one that only a pattern without the class id escaped would take, and that does not end as a part ends; plain appends; kept fragments in held parts and in one the class does not hold; two classes on one writer; aborts mid-class; and the deletion of staged parts by class ids that are wildcards themselves."""
    deflate = unit_index.deflate_fragment
    body = _shard_body
    yield "first-empty", None, {}, [
        ("open", "c", (), False, False),
        ("close",),
        ("take-pending",),
        ("commit",),
        ("entries",),
    ]
    yield "first-one", 48, {}, [
        ("open", "c", (), False, False),
        ("add", body(5, 1), 0),
        ("close",),
        ("commit",),
        ("entries",),
    ]
    yield (
        "first-cut",
        48,
        {},
        [
            ("open", "c", (), False, False),
            *(
                ("add", body(length, seed), 0)
                for seed, length in enumerate((10, 10, 10, 20, 23, 60, 1, 0, 40))
            ),
            ("close",),
            ("entries",),
            ("commit",),
            ("entries",),
        ],
    )
    yield (
        "first-inflating",
        48,
        {"units/c.json": _shard_part(body(5, 2))},
        [
            ("open", "c", ("units/c.json",), False, False),
            ("holds", "units/c.json"),
            ("add", deflate(body(30, 3)), 30),
            ("add", body(12, 4), 0),
            ("add", deflate(body(30, 5)), 29),
            ("add", deflate(body(30, 5)), 31),
            ("add", b"\xff\xff", 4),
            ("add", deflate(body(8, 6))[:-1], 8),
            ("add", deflate(body(8, 6)) + b"x", 8),
            ("add", deflate(b""), 1),
            ("close",),
            ("entries",),
        ],
    )
    yield (
        "fold",
        48,
        {"units/c.x000.json": _shard_part(body(5, 7))},
        [
            ("open", "c", ("units/c.x000.json",), False, True),
            ("add", body(30, 8), 0),
            ("add", deflate(body(40, 9)), 40),
            ("add", b"x" * 100, 0),
            ("add", b"", 0),
            ("add", body(3, 10), 0),
            ("close",),
            ("commit",),
            ("entries",),
        ],
    )
    base = _shard_part(body(10, 11), body(10, 12))
    yield (
        "append-room",
        64,
        {"units/c.json": base, "units/c.x000.json": _shard_part(body(10, 13))},
        [
            ("open", "c", ("units/c.json", "units/c.x000.json"), True, True),
            ("holds", "units/c.json"),
            ("holds", "units/d.json"),
            ("keep", "units/c.json", 12, 10, 0),
            ("keep", "units/c.x000.json", 1, 10, 7),
            ("keep", "units/zz.json", 1, 1, 0),
            ("add", body(20, 14), 0),
            ("add", deflate(body(20, 15)), 20),
            ("add", body(20, 16), 0),
            ("add", body(200, 17), 0),
            ("add", body(5, 18), 0),
            ("holds", "units/c.x001.json"),
            ("close",),
            ("take-pending",),
            ("entries",),
        ],
    )
    yield (
        "append-new",
        64,
        {"units/c.000.json": _shard_part(body(10, 19)), "units/c.001.json": _shard_part(body(10, 20))},
        [
            ("open", "c", ("units/c.000.json", "units/c.001.json"), True, True),
            *(("add", body(10, seed), 0) for seed in range(21, 27)),
            ("close",),
            ("commit",),
            ("entries",),
        ],
    )
    yield (
        "append-full",
        32,
        {"units/c.json": _shard_part(body(5, 27)), "units/c.x000.json": _shard_part(body(20, 28))},
        [
            ("open", "c", ("units/c.json", "units/c.x000.json"), True, True),
            ("add", body(30, 29), 0),
            ("add", body(2, 30), 0),
            ("close",),
            ("commit",),
            ("entries",),
        ],
    )
    yield (
        "append-plain",
        64,
        {"units/c.json": _shard_part(body(10, 31))},
        [
            ("open", "c", ("units/c.json",), True, False),
            ("add", body(10, 32), 0),
            ("add", deflate(body(10, 33)), 10),
            ("close",),
            ("commit",),
            ("entries",),
        ],
    )
    yield (
        "append-unordered",
        32,
        {
            "units/c.json": _shard_part(body(5, 34)),
            "units/c.x003.json": _shard_part(body(5, 35)),
            "units/c.x001.json": _shard_part(body(5, 36)),
        },
        [
            ("open", "c", ("units/c.json", "units/c.x003.json", "units/c.x001.json"), True, True),
            ("add", body(5, 37), 0),
            ("add", body(60, 38), 0),
            ("close",),
            ("take-pending",),
            ("entries",),
        ],
    )
    for name, parts in (
        ("append-wide", ("units/c.json", "units/c.x٠٠٢.json", "units/c.x1000.json")),
        ("append-digits", ("units/c.json", "units/c.x00.json", "units/c.x٠٠٢.json")),
    ):
        yield (
            name,
            32,
            {part: _shard_part(body(5, 39 + index)) for index, part in enumerate(parts)},
            [
                ("open", "c", parts, True, True),
                ("add", body(5, 42), 0),
                ("add", body(60, 43), 0),
                ("add", body(5, 44), 0),
                ("close",),
                ("commit",),
                ("entries",),
            ],
        )
    escaped = ("units/a.b+c.json", "units/a.b+c.x000.json", "units/aXb+c.x009.json")
    yield (
        "append-escaped",
        32,
        {part: _shard_part(body(5, 45 + index)) for index, part in enumerate(escaped)},
        [
            ("open", "a.b+c", escaped, True, True),
            ("add", body(5, 48), 0),
            ("add", body(60, 49), 0),
            ("close",),
            ("commit",),
            ("entries",),
        ],
    )
    yield (
        "append-bad-ending",
        64,
        {"units/c.json": _shard_part(body(5, 50)), "units/c.x000.json": b"[abc]"},
        [
            ("open", "c", ("units/c.json", "units/c.x000.json"), True, True),
            ("add", body(5, 51), 0),
            ("add", body(5, 52), 0),
            ("close",),
            ("entries",),
            ("abort",),
            ("entries",),
        ],
    )
    yield (
        "two-classes",
        None,
        {"units/b.json": _shard_part(body(5, 53))},
        [
            ("open", "a", (), False, False),
            ("add", body(5, 54), 0),
            ("add", body(6, 55), 0),
            ("close",),
            ("open", "b", ("units/b.json",), True, True),
            ("holds", "units/b.json"),
            ("keep", "units/b.json", 1, 5, 0),
            ("add", body(7, 56), 0),
            ("close",),
            ("take-pending",),
            ("commit",),
            ("entries",),
        ],
    )
    yield (
        "abort-direct",
        48,
        {},
        [
            ("open", "c", (), False, True),
            ("add", body(30, 57), 0),
            ("add", body(40, 58), 0),
            ("entries",),
            ("abort",),
            ("entries",),
        ],
    )
    yield (
        "abort-append",
        64,
        {"units/c.json": _shard_part(body(5, 59)), "units/c.x000.json": _shard_part(body(5, 60))},
        [
            ("open", "c", ("units/c.json", "units/c.x000.json"), True, True),
            ("add", body(5, 61), 0),
            ("entries",),
            ("abort",),
            ("entries",),
        ],
    )
    staged = {
        f"units/{name}": b"[]\n"
        for name in (
            "a.000.json.partial",
            "a..json.partial",
            "a.x000.json.partial",
            "a.000.json",
            "ab.000.json.partial",
            "b.1.json.partial",
            "[ab].1.json.partial",
            "a*.1.json.partial",
            "-.2.json.partial",
            "].3.json.partial",
            "^.4.json.partial",
            "!.4.json.partial",
            "a[.5.json.partial",
            "a-b.6.json.partial",
            "e.7.json.partial",
            "\\.8.json.partial",
            "&.9.json.partial",
            "·Tea.1.json.partial",
            "c.000.json",
        )
    }
    for index, class_ids in enumerate(
        (
            ("a",),
            ("[ab]",),
            ("[!a]",),
            ("a*",),
            ("?",),
            ("*",),
            ("a[",),
            ("[z-a]",),
            ("[!z-a]",),
            ("[a-c-e]",),
            ("[]a]",),
            ("[!]a]",),
            ("[a-]",),
            ("[-a]",),
            ("[!-a]",),
            ("[z-a!b]",),
            ("[z-a^b]",),
            ("[\\-]",),
            ("[&]",),
            ("[a-b-a]",),
            ("·Tea",),
            ("a-b", "e"),
            ("nothing",),
        )
    ):
        yield f"discard-{index}", None, staged, [("discard", class_ids), ("entries",)]


def _shards_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `shards` stage's generated lines, one a writer of `_shard_cases`, each under `generated:<name>`: its input the cap, the parts its directory holds and its calls, each part's and fragment's bytes kept in the dump's `SHARDS_BLOBS` under their sha256, and its value what `_shard_replay` gives over a fresh directory."""
    blobs = source.out / SHARDS_BLOBS
    for name, cap, held, ops in _shard_cases():
        kept = [(part, _blob_into(blobs, data)) for part, data in held.items()]
        calls = [("add", _blob_into(blobs, op[1]), op[2]) if op[0] == "add" else op for op in ops]
        with tempfile.TemporaryDirectory(prefix="shards-stage-") as directory:
            value = _shard_replay(Path(directory), cap, kept, calls, blobs)
        yield {"key": f"generated:{name}", "input": (cap, kept, calls), "value": value}


PRIOR_READ_STAGE = "prior-read"
# The `prior-read` stage's directory, beside its stage file, of the bytes its lines name by sha256: each part a read touched as the reader's directory held it, each body a compare was handed, and each arena span with the dictionary it inflates under.
PRIOR_READ_BLOBS = "prior-read.blobs"
# The blob directory of the build whose `prior-read` stage records, for the length of that build (`_prior_read_blobs_at`).
_PRIOR_READ_BLOBS_AT: list[Path] = []
# Each part a recorded read touched, by its path, inode, size and modification time, with its bytes' sha256, so a part read many times is read into a blob once.
_PRIOR_READ_HELD: dict[tuple[str, int, int, int], str] = {}
# The forms a `prior-read` line takes, by the `PriorFragmentReader` method each records.
PRIOR_READ_FORMS = {"read": "read", "read_bytes": "read-bytes", "same_bytes": "same-bytes"}
_PRIOR_READ_SIGNATURES = {
    name: inspect.signature(getattr(unit_cache.PriorFragmentReader, name)) for name in PRIOR_READ_FORMS
}
_ARENA_READ = inspect.signature(build._FragmentArena.read)


@contextlib.contextmanager
def _prior_read_blobs_at(directory: Path) -> Iterator[None]:
    _PRIOR_READ_BLOBS_AT.append(directory)
    try:
        yield
    finally:
        _PRIOR_READ_BLOBS_AT.pop()
        _PRIOR_READ_HELD.clear()


def _prior_located(located: unit_cache.PriorFragment) -> tuple:
    """A `unit_cache.PriorFragment` as a `prior-read` line holds it, its fields in declaration order."""
    return (
        located.part,
        located.start,
        located.length,
        located.unit_id,
        located.content_key,
        located.byte_copied,
        located.inflated,
    )


def _prior_held(directory: Path, part: str, blobs: Path) -> list[tuple[str, str]]:
    """The part a read at `part` opens under `directory`, as the line's held parts: `[(part, sha256)]` with its bytes kept in `blobs`, or none when `directory` holds no file there."""
    path = directory / part
    try:
        found = path.stat()
    except OSError:
        return []
    if not stat.S_ISREG(found.st_mode):
        return []
    held = (str(path), found.st_ino, found.st_size, found.st_mtime_ns)
    digest = _PRIOR_READ_HELD.get(held)
    if digest is None:
        digest = _PRIOR_READ_HELD[held] = _blob_into(blobs, path.read_bytes())
    return [(part, digest)]


def _prior_given(form: str, result: object) -> tuple:
    """What a read gave, as a `prior-read` line's value holds it: `("fragment", value)`, `("bytes", sha256)` or `("same", flag)`."""
    if form == "same-bytes":
        assert isinstance(result, bool), result
        return ("same", result)
    if form == "read-bytes":
        assert isinstance(result, bytes), result
        return ("bytes", hashlib.sha256(result).hexdigest())
    return ("fragment", result)


def _prior_outcome(form: str, read: Callable[[], object]) -> tuple:
    """What `read` gives as a `prior-read` line's value: `_prior_given`, or `("undecoded",)` where `json.loads` refuses the bytes, whose text the Rust reader words in its own way (as the `json-forms` stage compares its refusals), `("raised", "ValueError", message)` for a refusal of the reader's own, and `("os-error", errno)`."""
    try:
        result = read()
    except json.JSONDecodeError, UnicodeDecodeError:
        return ("undecoded",)
    except ValueError as error:
        return ("raised", "ValueError", str(error))
    except OSError as error:
        return ("os-error", error.errno)
    return _prior_given(form, result)


def _prior_read_line(origin: str, inputs: tuple, value: tuple) -> dict[str, object]:
    digest = hashlib.sha1(compact(encode(inputs)).encode("ascii")).hexdigest()[:20]
    return {"key": f"{origin}:{inputs[0]}:{digest}", "input": inputs, "value": value}


def _prior_reading(name: str) -> Recorder:
    """A recorder of `PriorFragmentReader.<name>`: the read's form, the part it opens as the reader's directory holds it, its address and, for a compare, the body as a blob, with what it gave."""
    form = PRIOR_READ_FORMS[name]
    signature = _PRIOR_READ_SIGNATURES[name]

    def record(args: tuple, kwargs: dict) -> Finisher:
        bound = signature.bind(*args, **kwargs)
        reader: unit_cache.PriorFragmentReader = bound.arguments["self"]
        located: unit_cache.PriorFragment = bound.arguments["located"]
        blobs = _PRIOR_READ_BLOBS_AT[-1]
        inputs: tuple = (form, _prior_held(reader._out_dir, located.part, blobs), _prior_located(located))
        if form == "same-bytes":
            inputs += (_blob_into(blobs, bytes(bound.arguments["body"])),)
        return lambda result: _prior_read_line("run", inputs, _prior_given(form, result))

    return record


def _arena_read(args: tuple, kwargs: dict) -> Finisher:
    """`build._FragmentArena.read`: the arena's dictionary and the span's bytes as blobs, and the address, with the fragment it gave."""
    bound = _ARENA_READ.bind(*args, **kwargs)
    arena: build._FragmentArena = bound.arguments["self"]
    located: unit_cache.PriorFragment = bound.arguments["located"]
    blobs = _PRIOR_READ_BLOBS_AT[-1]
    packed = bytes(arena._bytes[located.start : located.start + located.length])
    inputs = (
        "arena",
        _blob_into(blobs, arena.dictionary),
        _blob_into(blobs, packed),
        _prior_located(located),
    )
    return lambda result: _prior_read_line("run", inputs, ("fragment", result))


def _prior_fragment(unit_id: object, content_key: object = "k-0", **fields: object) -> bytes:
    """A generated fragment's plain shard bytes (`build._fragment_bytes`), holding `unit_id` and `content_key` and `fields` after them, and no `content_key` for `content_key=...`."""
    fragment: dict[str, object] = {"id": unit_id}
    if content_key is not ...:
        fragment["content_key"] = content_key
    fragment.update(fields)
    return build._fragment_bytes(fragment)


def _prior_read_cases() -> Iterator[tuple[str, dict[str, bytes], tuple]]:
    """The `prior-read` stage's generated reads, each its name, the parts its directory holds and its call, `(form, address, ...)` with the address `(part, start, length, unit_id, content_key, byte_copied, inflated)`: plain and deflated fragments read, read as bytes and compared, whole and cut short or run on, under their own id and stamp and another, holding no stamp, a non-ASCII id, a repeated key, a value that is not a dict or no JSON at all, at a missing part, a directory and past a part's end, at negative offsets and lengths, deflated under every check of `unit_index.inflate_fragment` and the lengths it reads as none or refuses; and arena spans under the right dictionary, another, none at all, cut short, run on, holding another unit or no JSON."""
    closing = unit_index.PART_CLOSING
    first = _prior_fragment("u-a", "k-a", note="one")
    second = _prior_fragment("u-b", None, note="two")
    third = _prior_fragment("u-·c", "k-c")
    fourth = _prior_fragment("u-d", ..., note="no stamp")
    repeated = b'{"id": "u-x", "content_key": "k-x", "id": "u-e"}'
    listed = b'["u-f"]'
    broken = b'{"id": "u-g", "content_key": '
    invalid = b'{"id": "u-\xff"}'
    plain_bodies = [first, second, third, fourth, repeated, listed, broken, invalid]
    plain = b"[" + b",".join(plain_bodies) + closing
    starts: list[int] = []
    at = 1
    for body in plain_bodies:
        starts.append(at)
        at += len(body) + 1
    parts = {"units/c.json": plain}

    def address(index: int, unit_id: str, content_key: str | None, *, byte_copied: bool = True) -> tuple:
        return ("units/c.json", starts[index], len(plain_bodies[index]), unit_id, content_key, byte_copied, 0)

    for form in ("read", "read-bytes"):
        for byte_copied in (True, False):
            mark = "copied" if byte_copied else "walked"
            yield f"{form}-{mark}-first", parts, (form, address(0, "u-a", "k-a", byte_copied=byte_copied))
            yield f"{form}-{mark}-other-id", parts, (form, address(0, "u-z", "k-a", byte_copied=byte_copied))
            yield f"{form}-{mark}-other-stamp", parts, (
                form,
                address(0, "u-a", "k-z", byte_copied=byte_copied),
            )
            yield f"{form}-{mark}-no-stamp", parts, (form, address(0, "u-a", None, byte_copied=byte_copied))
        yield f"{form}-null-stamp", parts, (form, address(1, "u-b", None))
        yield f"{form}-null-stamp-other", parts, (form, address(1, "u-b", "k-b"))
        yield f"{form}-non-ascii-id", parts, (form, address(2, "u-·c", "k-c"))
        yield f"{form}-missing-stamp", parts, (form, address(3, "u-d", None))
        yield f"{form}-missing-stamp-other", parts, (form, address(3, "u-d", "k-d"))
        yield f"{form}-repeated-last", parts, (form, address(4, "u-e", "k-x"))
        yield f"{form}-repeated-first", parts, (form, address(4, "u-x", "k-x"))
        yield f"{form}-list", parts, (form, address(5, "u-f", None))
        yield f"{form}-broken", parts, (form, address(6, "u-g", None))
        yield f"{form}-not-utf-8", parts, (form, address(7, "u-\xff", None))
        yield f"{form}-cut", parts, (form, ("units/c.json", starts[0], len(first) - 3, "u-a", "k-a", True, 0))
        yield f"{form}-run-on", parts, (
            form,
            ("units/c.json", starts[0], len(first) + 1, "u-a", "k-a", True, 0),
        )
        yield f"{form}-past-end", parts, (form, ("units/c.json", len(plain) + 5, 10, "u-a", "k-a", True, 0))
        yield f"{form}-tail", parts, (form, ("units/c.json", len(plain) - 4, 10, "u-a", "k-a", True, 0))
        yield f"{form}-empty", parts, (form, ("units/c.json", starts[0], 0, "u-a", "k-a", True, 0))
        yield f"{form}-missing-part", parts, (form, ("units/gone.json", 1, 10, "u-a", "k-a", True, 0))
        yield f"{form}-directory", parts, (form, ("units", 1, 10, "u-a", "k-a", True, 0))
        yield f"{form}-negative-start", parts, (form, ("units/c.json", -1, 10, "u-a", "k-a", True, 0))
        yield f"{form}-negative-length", parts, (form, ("units/c.json", 1, -1, "u-a", "k-a", True, 0))
    deflate = unit_index.deflate_fragment
    stored = [
        deflate(first),
        deflate(second),
        deflate(fourth),
        deflate(listed),
        deflate(first)[:-2],
        deflate(first) + b"\x00",
        b"\x02\xff\xff",
        deflate(b""),
    ]
    packed_part = b"[" + b",".join(stored) + closing
    stored_starts: list[int] = []
    at = 1
    for body in stored:
        stored_starts.append(at)
        at += len(body) + 1
    deflated_parts = {"units/c.x000.json": packed_part}

    def deflated(index: int, inflated: int, unit_id: str = "u-a", content_key: str | None = "k-a") -> tuple:
        return (
            "units/c.x000.json",
            stored_starts[index],
            len(stored[index]),
            unit_id,
            content_key,
            True,
            inflated,
        )

    for form in ("read", "read-bytes"):
        yield f"{form}-deflated", deflated_parts, (form, deflated(0, len(first)))
        yield f"{form}-deflated-null-stamp", deflated_parts, (form, deflated(1, len(second), "u-b", None))
        yield f"{form}-deflated-missing-stamp", deflated_parts, (form, deflated(2, len(fourth), "u-d", None))
        yield f"{form}-deflated-list", deflated_parts, (form, deflated(3, len(listed), "u-f", None))
        yield f"{form}-deflated-other-id", deflated_parts, (form, deflated(0, len(first), "u-z"))
        yield f"{form}-deflated-short", deflated_parts, (form, deflated(0, len(first) - 1))
        yield f"{form}-deflated-long", deflated_parts, (form, deflated(0, len(first) + 1))
        yield f"{form}-deflated-cut", deflated_parts, (form, deflated(4, len(first)))
        yield f"{form}-deflated-run-on", deflated_parts, (form, deflated(5, len(first)))
        yield f"{form}-deflated-garbage", deflated_parts, (form, deflated(6, 4))
        yield f"{form}-deflated-empty", deflated_parts, (form, deflated(7, 1))
        yield f"{form}-deflated-span-run-on", deflated_parts, (
            form,
            ("units/c.x000.json", stored_starts[0], len(stored[0]) + 1, "u-a", "k-a", True, len(first)),
        )
        for inflated in (-1, -2):
            yield f"{form}-deflated-inflated{inflated}", deflated_parts, (form, deflated(0, inflated))
            yield f"{form}-deflated-garbage-inflated{inflated}", deflated_parts, (form, deflated(6, inflated))
    both = parts | deflated_parts
    yield "same-plain", both, ("same-bytes", address(0, "u-a", "k-a"), first)
    yield "same-plain-other", both, ("same-bytes", address(0, "u-a", "k-a"), first[:-1] + b"!")
    yield "same-plain-length", both, ("same-bytes", address(0, "u-a", "k-a"), first + b" ")
    yield "same-plain-length-missing", both, (
        "same-bytes",
        ("units/gone.json", 1, len(first), "u-a", "k-a", True, 0),
        first + b" ",
    )
    yield "same-plain-missing", both, (
        "same-bytes",
        ("units/gone.json", 1, len(first), "u-a", "k-a", True, 0),
        first,
    )
    yield "same-plain-past-end", both, (
        "same-bytes",
        ("units/c.json", len(plain) - 4, len(first), "u-a", "k-a", True, 0),
        first,
    )
    yield "same-deflated", both, ("same-bytes", deflated(0, len(first)), first)
    yield "same-deflated-other", both, ("same-bytes", deflated(0, len(first)), first[:-1] + b"!")
    yield "same-deflated-length", both, ("same-bytes", deflated(0, len(first)), first + b" ")
    yield "same-deflated-length-missing", both, (
        "same-bytes",
        ("units/gone.json", 1, 10, "u-a", "k-a", True, len(first)),
        first + b" ",
    )
    yield "same-deflated-missing", both, (
        "same-bytes",
        ("units/gone.json", 1, 10, "u-a", "k-a", True, len(first)),
        first,
    )
    yield "same-deflated-cut", both, ("same-bytes", deflated(4, len(first)), first)
    yield "same-deflated-garbage", both, ("same-bytes", deflated(6, len(first)), first)
    yield "same-empty", both, ("same-bytes", ("units/c.json", starts[0], 0, "u-a", "k-a", True, 0), b"")
    dictionary = build.FRAGMENT_DICTIONARY.read_bytes()

    def packed(body: bytes, zdict: bytes | None = dictionary) -> bytes:
        compressor = zlib.compressobj(1) if zdict is None else zlib.compressobj(1, zdict=zdict)
        return compressor.compress(body) + compressor.flush()

    arena_cases: list[tuple[str, bytes, bytes, int, str, str | None]] = [
        ("good", dictionary, packed(first), 0, "u-a", "k-a"),
        ("good-later", dictionary, packed(first), 977, "u-a", "k-a"),
        ("null-stamp", dictionary, packed(second), 0, "u-b", None),
        ("null-stamp-other", dictionary, packed(second), 0, "u-b", "k-b"),
        ("missing-stamp", dictionary, packed(fourth), 0, "u-d", None),
        ("other-id", dictionary, packed(first), 0, "u-z", "k-a"),
        ("other-stamp", dictionary, packed(first), 0, "u-a", "k-z"),
        ("non-ascii-id", dictionary, packed(third), 0, "u-·c", "k-c"),
        ("repeated", dictionary, packed(repeated), 0, "u-e", "k-x"),
        ("list", dictionary, packed(listed), 0, "u-f", None),
        ("broken", dictionary, packed(broken), 0, "u-g", None),
        ("not-utf-8", dictionary, packed(invalid), 0, "u-\xff", None),
        ("other-dictionary", b"another dictionary", packed(first), 0, "u-a", "k-a"),
        ("no-dictionary", dictionary, packed(first, None), 0, "u-a", "k-a"),
        ("dictionary-unasked", b"", packed(first, None), 0, "u-a", "k-a"),
        ("cut", dictionary, packed(first)[:-3], 0, "u-a", "k-a"),
        ("header-only", dictionary, packed(first)[:6], 0, "u-a", "k-a"),
        ("run-on", dictionary, packed(first) + b"x", 0, "u-a", "k-a"),
        ("raw", dictionary, deflate(first), 0, "u-a", "k-a"),
        ("garbage", dictionary, b"\x00\x01\x02\x03", 0, "u-a", "k-a"),
        ("empty", dictionary, b"", 0, "u-a", "k-a"),
    ]
    for name, zdict, span, lead, unit_id, content_key in arena_cases:
        yield f"arena-{name}", {}, ("arena", zdict, span, lead, unit_id, content_key)


def _prior_read_case(directory: Path, call: tuple, blobs: Path) -> tuple[tuple, tuple]:
    """Make one generated read of `_prior_read_cases` over `directory`, which holds its parts, and return the line's input and value: the read made through a `unit_cache.PriorFragmentReader` over `directory`, or through a `build._FragmentArena` over the dictionary, holding a lead of that many bytes before the span."""
    match call:
        case ("arena", bytes(zdict), bytes(span), int(lead), str(unit_id), content_key):
            arena = build._FragmentArena(zdict)
            if lead:
                arena.add(bytes(lead), lead)
            part, start, length = arena.add(span, len(span))
            located = unit_cache.PriorFragment(part, start, length, unit_id, content_key)
            inputs: tuple = (
                "arena",
                _blob_into(blobs, zdict),
                _blob_into(blobs, span),
                _prior_located(located),
            )
            return inputs, _prior_outcome("arena", lambda: arena.read(located))
        case (str(form), tuple(fields), *rest):
            located = unit_cache.PriorFragment(*fields[:5], byte_copied=fields[5], inflated=fields[6])
            held = sorted(
                (part.relative_to(directory).as_posix(), _blob_into(blobs, part.read_bytes()))
                for part in directory.rglob("*")
                if part.is_file()
            )
            inputs = (form, held, _prior_located(located))
            with unit_cache.PriorFragmentReader(directory) as reader:
                if form == "same-bytes":
                    (body,) = rest
                    inputs += (_blob_into(blobs, body),)
                    return inputs, _prior_outcome(form, lambda: reader.same_bytes(located, body))
                read = reader.read if form == "read" else reader.read_bytes
                return inputs, _prior_outcome(form, lambda: read(located))
    raise TypeError(f"no prior read {call!r:.120}")


def _prior_read_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `prior-read` stage's generated lines, one a read of `_prior_read_cases`, each under `generated:<name>`, its parts, bodies, spans and dictionaries kept in the dump's `PRIOR_READ_BLOBS` under their sha256, and its value what the Python reader gave over a fresh directory holding the case's parts."""
    blobs = source.out / PRIOR_READ_BLOBS
    for name, parts, call in _prior_read_cases():
        with tempfile.TemporaryDirectory(prefix="prior-read-stage-") as scratch:
            directory = Path(scratch)
            for part, data in parts.items():
                (directory / part).parent.mkdir(parents=True, exist_ok=True)
                (directory / part).write_bytes(data)
            inputs, value = _prior_read_case(directory, call, blobs)
        yield {"key": f"generated:{name}", "input": inputs, "value": value}


EMIT_STAGE = "emit"
_EMISSION = inspect.signature(build._emission)


def _emitted(args: tuple, kwargs: dict) -> Finisher:
    """`build._emission`: the unit at its ordinal as the write receives it, under `unit:<ordinal>`, valued `_emission_value`. The dump's write runs at one job, so the ordinal is the full table's."""
    bound = _EMISSION.bind(*args, **kwargs)
    ordinal: int = bound.arguments["ordinal"]
    table: UnitTable = bound.arguments["table"]

    def finish(emission: object) -> Mapping[str, object]:
        assert isinstance(emission, build._Emission), emission
        return {"key": f"unit:{ordinal}", "value": _emission_value(emission, table, ordinal)}

    return finish


def _emission_value(emission: build._Emission, table: UnitTable, ordinal: int) -> tuple:
    """An emission as an `emit` line's value: its kind, `fragment` or `bytes`, its unit id, content key and policy file, the sha256 of the bytes the write takes from it (a fragment's `build._fragment_bytes`, or the stored bytes as they are), the config note `_write_chunk` keeps for it (the fragment's, or `build.config_note` over the table's configurations for stored bytes), and, for stored bytes, their address and the identity that stands in for the fragment."""
    if emission.fragment is not None:
        body = build._fragment_bytes(emission.fragment)
        note = emission.fragment["config_note"]
        address = identity = None
    else:
        assert emission.body is not None and emission.source is not None
        body = emission.body
        note = build.config_note(table.configs(ordinal), ACCEPTANCE_CONFIGS)
        address = _prior_located(emission.source)
        identity = emission.identity
    return (
        "fragment" if emission.fragment is not None else "bytes",
        emission.unit_id,
        emission.content_key,
        emission.policy_file,
        hashlib.sha256(body).hexdigest(),
        note,
        address,
        identity,
    )


CHECK_PATCHED_STAGE = "check-patched"
# The smallest fragment of each shape (`_patched_shape`) the `check-patched` stage's build checks, by shape, with its encoded length: the bases its generated set mutates, filled by the build that precedes the generators in `build_dump`.
_CHECK_PATCHED: dict[str, tuple[int, dict]] = {}
# The fragment keys `check_unit` reads at `PATCHED`, which are the only ones the stage's generic mutations change, and how many levels below each they reach.
PATCHED_DEPTHS: dict[str, int] = {
    "id": 0,
    **{check_id: 0 for check_id in audit.MACHINE_CHECKS},
    "no_verdict": 0,
    "duplicate_group": 0,
    "cluster": 0,
    "pair": 1,
    "secondary_junctions": 3,
}
PATCHED_RECT = {"x_min": 0, "x_max": 1, "advance_total": 2}
PATCHED_JUNCTION = {
    "pair": {"left": 0, "right": 1},
    "before": PATCHED_RECT,
    "after": PATCHED_RECT,
    "primary_unit": "u-1",
}


def _patched_shape(fragment: dict) -> str:
    """What sorts a patched fragment into a base of the `check-patched` stage's generated set: its machine flags and exemption, and whether it carries a pair, secondary junctions, and a junction without a primary unit."""
    junctions = fragment.get("secondary_junctions")
    return compact(
        encode(
            [
                [fragment.get(check_id) is True for check_id in audit.MACHINE_CHECKS],
                fragment.get("no_verdict") is True,
                fragment.get("pair") is None,
                junctions is None,
                isinstance(junctions, list)
                and any(
                    isinstance(junction, dict) and junction.get("primary_unit") is None
                    for junction in junctions
                ),
            ]
        )
    )


def _patched_line(origin: str, fragment: object) -> dict[str, object]:
    try:
        value: object = build.check_unit(
            fragment,  # pyright: ignore[reportArgumentType]
            "m1-audit",
            at=(build.PATCHED,),
        )
    except CHECK_RAISES as error:
        value = {"raised": type(error).__name__}
    return {"key": _digest_key(f"{origin}:", {"fragment": fragment}), "fragment": fragment, "value": value}


def _patched_checked(args: tuple, kwargs: dict) -> Finisher | None:
    """`build.check_unit` at `PATCHED`, as `_CorpusCheck.unit` calls it in the write: the fragment under `patched:` and a digest of it, valued the error list. The smallest fragment of each shape is kept as a base of the generated set (`_CHECK_PATCHED`)."""
    if len(args) != 2 or args[1] != "m1-audit" or kwargs != {"at": (build.PATCHED,)}:
        return None
    fragment = args[0]
    text = compact(encode({"fragment": fragment}))
    shape = _patched_shape(fragment)
    held = _CHECK_PATCHED.get(shape)
    if held is None or len(text) < held[0]:
        _CHECK_PATCHED[shape] = (len(text), copy.deepcopy(fragment))
    key = "patched:" + hashlib.sha1(text.encode("ascii")).hexdigest()[:20]
    return lambda errors: {"key": key, "fragment": fragment, "value": errors}


def _patched_extras(fragment: dict) -> Iterator[object]:
    """The `check-patched` stage's targeted mutations of `fragment`, one or more for each predicate the generic ones do not reach: values that are not a dict, ids of every kind `str` writes, every combination of the machine flags and the exemption with duplicate groups and clusters of each kind, secondary-junction lists of every shape on units that are and are not ink- or picture-identical, junction pairs against primary pairs, broken rectangles and primary units of each kind."""

    def put(**changes: object) -> dict:
        return {**fragment, **changes}

    yield from ([fragment], "fragment", None, 5)
    for unit_id in (None, 5, True, ["u-1"], ("u-1",), {"id": 1}, "<missing>", "u-ü"):
        yield put(id=unit_id, duplicate_group=5, cluster=5)
    for flags in product((True, False), repeat=4):
        for group, cluster in product((None, "e-1", "E-1", 5), (None, "c-1", "C-1", 5)):
            yield put(
                **dict(zip((*audit.MACHINE_CHECKS, "no_verdict"), flags)),
                duplicate_group=group,
                cluster=cluster,
            )
    junction = PATCHED_JUNCTION
    for junctions in (
        None,
        [],
        (),
        "x",
        5,
        junction,
        [junction],
        (junction,),
        [junction, junction],
        [junction, "x"],
        [junction, None],
        [[junction]],
        *([{key: value for key, value in junction.items() if key != dropped}] for dropped in junction),
        [{**junction, "extra": 1}],
        [junction, {**junction, "pair": None}],
    ):
        for ink_identical, picture_identical in ((False, False), (True, False), (False, True), (1, None)):
            yield put(
                secondary_junctions=junctions,
                ink_identical=ink_identical,
                picture_identical=picture_identical,
            )
    for junction_pair in (
        {"left": 0, "right": 1},
        {"left": 1, "right": 1},
        {"left": 2, "right": 1},
        {"left": True, "right": 2},
        {"left": "0", "right": 1},
        {"left": 0},
        {"right": 1},
        {},
        None,
        [0, 1],
        5,
    ):
        for primary_pair in (
            {"left": 0, "right": 1},
            {"left": False, "right": 1},
            {"left": 0},
            {},
            {"left": 0, "right": 2},
            None,
            [0, 1],
            "pair",
        ):
            yield put(pair=primary_pair, secondary_junctions=[{**junction, "pair": junction_pair}])
    for broken in (
        {"x_min": 5, "x_max": 1, "advance_total": 9},
        {"x_min": 0, "x_max": 9, "advance_total": 1},
        {"x_min": False, "x_max": True, "advance_total": 1},
        {"x_min": 0, "x_max": 1},
        {"x_min": 0, "x_max": 1, "advance_total": "2"},
        {"x_min": -1, "x_max": -1, "advance_total": -1},
        None,
        [PATCHED_RECT],
        "rect",
    ):
        for side in ("before", "after"):
            yield put(secondary_junctions=[{**junction, side: broken}])
    for primary_unit in (None, "u-", "u-1", "e-1", "U-1", "", 5, ["u-1"]):
        yield put(secondary_junctions=[{**junction, "primary_unit": primary_unit}])


def _check_patched_generated(_source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `check-patched` stage's generated lines, under `generated:` and a digest of the fragment: the generic mutations of the keys the check reads (`_check_mutations` over `PATCHED_DEPTHS`) and the targeted ones (`_patched_extras`) of the smallest patched fragment of each shape the build checked (`_CHECK_PATCHED`), each checked by `check_unit` at `PATCHED`, with what it returns or the class of the exception it raises."""
    for shape in sorted(_CHECK_PATCHED):
        base = _CHECK_PATCHED[shape][1]
        for mutated in chain(_check_mutations(base, PATCHED_DEPTHS, PATCHED_DEPTHS), _patched_extras(base)):
            yield _patched_line("generated", mutated)


CORPUS_CHECK_STAGE = "corpus-check"


@dataclass
class _CheckChunk:
    """What one of the write's run checkers has been fed since it last handed its facts over: the class record it opened, if any, each unit with whether the cache supplied it, whether it closed the class, and the class's id and the units of it fed before."""

    meta: object = None
    opened: bool = False
    units: list[tuple[object, object]] = field(default_factory=list)
    end: bool = False
    class_id: object = None
    offset: int = 0


# What the `corpus-check` stage's recorders hold while the build writes: each run checker's chunk in progress, by the checker's id; each handed-over checker's chunk key, by its id, until the folding checker absorbs it; the keys each folding checker absorbed, in order, by its id; each chunk's facts, by key; and each finish's settings, chunk keys and manifest, for the generated lines (`_corpus_check_generated`).
_CHECK_RUNS: dict[int, _CheckChunk] = {}
_CHECK_TAKEN: dict[int, str] = {}
_CHECK_ABSORBED: dict[int, list[str]] = {}
_CHECK_CHUNKS: dict[str, build._CorpusCheck] = {}
_CHECK_FINISHED: list[tuple[dict[str, object], list[str], object]] = []
_CHECK_CLASS_START = inspect.signature(build._CorpusCheck.class_start)
_CHECK_UNIT = inspect.signature(build._CorpusCheck.unit)
_CHECK_FINISH = inspect.signature(build._CorpusCheck.finish)


def _writes_check(check: build._CorpusCheck) -> bool:
    """Whether `check` is one of the write's checkers, which check at `PATCHED` alone, rather than `check_shards`'."""
    return check._mode == "m1-audit" and check._at == (build.PATCHED,)


def _check_facts(check: build._CorpusCheck) -> dict[str, object]:
    """A copy of what `check` has accumulated, `_CorpusCheck._FACTS` by name without its underscore."""
    return copy.deepcopy({name.removeprefix("_"): getattr(check, name) for name in build._CorpusCheck._FACTS})


def _check_class_start(args: tuple, kwargs: dict) -> None:
    bound = _CHECK_CLASS_START.bind(*args, **kwargs)
    check = bound.arguments["self"]
    if _writes_check(check):
        chunk = _CHECK_RUNS.setdefault(id(check), _CheckChunk())
        if chunk.opened or chunk.units:
            raise ValueError("a run checker opened a class in a chunk it had already fed")
        meta = copy.deepcopy(bound.arguments["meta"])
        chunk.meta, chunk.opened, chunk.class_id, chunk.offset = meta, True, meta.get("id"), 0


def _check_fed(args: tuple, kwargs: dict) -> None:
    bound = _CHECK_UNIT.bind(*args, **kwargs)
    check = bound.arguments["self"]
    if _writes_check(check):
        _CHECK_RUNS.setdefault(id(check), _CheckChunk()).units.append(
            (copy.deepcopy(bound.arguments["unit"]), bound.arguments.get("cached"))
        )


def _check_class_end(args: tuple, _kwargs: dict) -> None:
    if _writes_check(args[0]):
        _CHECK_RUNS.setdefault(id(args[0]), _CheckChunk()).end = True


def _check_take_facts(args: tuple, _kwargs: dict) -> Finisher | None:
    """`_CorpusCheck.take_facts` on a run checker: a `chunk:` line under the class's id and the units of it fed before, with the class record it opened, the units it was fed, whether it closed the class, and the ids among its units the checker keeps identities for (`identity_for`), valued the facts it hands over."""
    check = args[0]
    if not _writes_check(check):
        return None
    chunk = _CHECK_RUNS.setdefault(id(check), _CheckChunk())
    identity_for = check._identity_for

    def finish(facts: object) -> Mapping[str, object]:
        assert isinstance(facts, build._CorpusCheck)
        key = f"chunk:{chunk.class_id}:{chunk.offset}"
        ids = None
        if identity_for is not None:
            fed = (unit.get("id") for unit, _cached in chunk.units if isinstance(unit, dict))
            ids = sorted({unit_id for unit_id in fed if isinstance(unit_id, str) and unit_id in identity_for})
        line = {
            "key": key,
            "meta": chunk.meta if chunk.opened else None,
            "units": chunk.units,
            "end": chunk.end,
            "identity_for": ids,
            "value": _check_facts(facts),
        }
        _CHECK_TAKEN[id(facts)] = key
        _CHECK_CHUNKS[key] = copy.deepcopy(facts)
        chunk.offset += len(chunk.units)
        chunk.units, chunk.opened, chunk.end = [], False, False
        return line

    return finish


def _check_absorb(args: tuple, _kwargs: dict) -> None:
    check, other = args[0], args[1]
    if _writes_check(check) and id(other) in _CHECK_TAKEN:
        _CHECK_ABSORBED.setdefault(id(check), []).append(_CHECK_TAKEN.pop(id(other)))


def _check_finished(args: tuple, kwargs: dict) -> Finisher | None:
    """`_CorpusCheck.finish` on the write's folding checker: a `finish:` line with the chunk keys it absorbed, in order, and the manifest, valued the facts it folded and the errors `finish` returns."""
    bound = _CHECK_FINISH.bind(*args, **kwargs)
    check = bound.arguments["self"]
    if not _writes_check(check):
        return None
    keys = _CHECK_ABSORBED.pop(id(check), [])
    facts = _check_facts(check)
    manifest = copy.deepcopy(bound.arguments["manifest"])
    settings = {
        "descriptions": check._descriptions,
        "batch_size": check._batch_size,
        "repo_root": check._repo_root,
    }
    _CHECK_FINISHED.append((settings, keys, manifest))
    key = f"finish:{len(_CHECK_FINISHED)}"
    return lambda errors: {
        "key": key,
        "chunks": keys,
        "manifest": manifest,
        "value": {"facts": facts, "errors": list(errors)},  # pyright: ignore[reportArgumentType]
    }


def _check_outcome(check: Callable[[], object]) -> object:
    """What `check` returns, or `{"raised": name}` for the exception Python raises."""
    try:
        return check()
    except (*CHECK_RAISES, KeyError) as error:
        return {"raised": type(error).__name__}


def _check_folded(
    parent: build._CorpusCheck, taken: Iterable[build._CorpusCheck], manifest: object
) -> object:
    """Fold `taken` into `parent` in order and finish it over `manifest`: the folded facts and the errors."""
    for facts in taken:
        parent.absorb(facts)
    folded = _check_facts(parent)
    return {"facts": folded, "errors": list(parent.finish(manifest))}  # pyright: ignore[reportArgumentType]


def _write_checker(
    settings: Mapping[str, object], identity_for: Collection[str] | None = None
) -> build._CorpusCheck:
    """A checker as the write makes one, over `settings`' descriptions, batch size and repo root."""
    root = settings["repo_root"]
    return build._CorpusCheck(
        mode="m1-audit",
        descriptions=settings["descriptions"],  # pyright: ignore[reportArgumentType]
        batch_size=settings["batch_size"],
        repo_root=None if root is None else Path(root),  # pyright: ignore[reportArgumentType]
        cached_ids=(),
        at=(build.PATCHED,),
        identity_for=None if identity_for is None else frozenset(identity_for),
    )


def _manifest_mutations(manifest: dict) -> Iterator[tuple[str, object]]:
    """The build's manifest with each predicate `finish` checks it by broken once: its totals, its triage index, its machine-approved counts, its classes and their batches, and its secondary-junction counts, each moved, dropped or of another type."""

    def edit(
        name: str, path: tuple[object, ...], change: Callable[[Any], object] | None
    ) -> tuple[str, object]:
        mutated = copy.deepcopy(manifest)
        held: Any = mutated
        for step in path[:-1]:
            held = held[step]
        if change is None:
            del held[path[-1]]
        else:
            held[path[-1]] = change(held[path[-1]])
        return name, mutated

    def plus(value: Any) -> object:
        return value + 1

    yield "manifest-empty", {}
    yield "manifest-list", []
    for name in ("units", "rows", "batches"):
        yield edit(f"totals-{name}", ("totals", name), plus)
    yield edit("totals-dropped", ("totals",), None)
    yield edit("totals-none", ("totals",), lambda _old: None)
    yield edit("index-reversed", ("human_unit_ids",), lambda old: list(reversed(old)))
    yield edit("index-short", ("human_unit_ids",), lambda old: old[1:])
    yield edit("index-extra", ("human_unit_ids",), lambda old: [*old, "u-extra"])
    yield edit("index-tuple", ("human_unit_ids",), lambda old: tuple(old))
    yield edit("index-non-str", ("human_unit_ids",), lambda old: [*old, None])
    yield edit("index-none", ("human_unit_ids",), lambda _old: None)
    yield edit("machine-units", ("machine_approved", "units"), plus)
    yield edit("machine-none", ("machine_approved",), lambda _old: None)
    yield edit("machine-str", ("machine_approved",), lambda _old: "x")
    yield edit("by-class-empty", ("machine_approved", "by_class"), lambda _old: {})
    yield edit("by-class-extra", ("machine_approved", "by_class"), lambda old: {**old, "extra": 1})
    yield edit("by-class-reversed", ("machine_approved", "by_class"), lambda old: dict(reversed(old.items())))
    yield edit("classes-reversed", ("classes",), lambda old: list(reversed(old)))
    yield edit("classes-none", ("classes",), lambda _old: None)
    yield edit("classes-repeated", ("classes",), lambda old: [*old, *old[:1]])
    for index, meta in enumerate(manifest.get("classes") or ()):
        if meta.get("batches"):
            yield edit(f"class-batches:{index}", ("classes", index, "batches"), lambda _old: [7])
            yield edit(f"class-batches-tuple:{index}", ("classes", index, "batches"), lambda old: tuple(old))
            break
    for name in ("units_with_markers", "junctions_with_primary_unit", "junctions_without_primary_unit"):
        yield edit(f"junctions-{name}", ("secondary_junctions", name), plus)
    yield edit("junctions-none", ("secondary_junctions",), lambda _old: None)
    yield edit("junctions-empty", ("secondary_junctions",), lambda _old: {})


# The policy file the `corpus-check` stage's generated cases name, which is a file in the repo.
CHECK_CASE_POLICY = "glyph_data/runes/qsPea.yaml"
CHECK_CASE_BATCH = 2


@dataclass
class _CheckCase:
    """One generated case of the `corpus-check` stage: the run checker's settings (`_write_checker`, with `identity_for`), the chunks fed to it in order, each a class record or None, its units as `[unit, cached]`, and whether it closes the class, and the manifest the folding checker finishes over."""

    settings: dict[str, Any]
    chunks: list[dict[str, Any]]
    manifest: Any

    def fed(self, unit_id: str) -> list:
        for chunk in self.chunks:
            for fed in chunk["units"]:
                if isinstance(fed[0], dict) and fed[0].get("id") == unit_id:
                    return fed
        raise KeyError(unit_id)

    def set(self, unit_id: str, **fields: object) -> None:
        self.fed(unit_id)[0].update(fields)

    def meta(self, class_id: str) -> dict:
        return next(meta for meta in self.manifest["classes"] if meta["id"] == class_id)


def _case_unit(unit_id: str, class_id: str, **fields: object) -> dict:
    """A unit as the write feeds a cached one (`build._cached_identity`), in a human class's default window."""
    return {
        "id": unit_id,
        "ink_identical": False,
        "picture_identical": False,
        "junior_equivalent": False,
        "no_verdict": False,
        "duplicate_group": None,
        "cluster": None,
        "class": class_id,
        "group": "qsPea:qsTea",
        "codepoints": "E650:E652",
        "configs": ["default"],
        "config_gate": None,
        "pair": {"left": 0, "right": 1},
        "secondary_junctions": None,
        "drafts": None,
    } | fields


def _check_base() -> _CheckCase:
    """The generated cases' base: three classes, a human one fed in two chunks with a machine-approved unit, a config-gated one, a unit naming a primary unit and one with a junction naming none, a no-verdict one, and a human one of a single unit; and the manifest that matches them, which the base passes."""
    classes = [
        (
            "A",
            False,
            [
                [
                    _case_unit(
                        "u-a1",
                        "A",
                        duplicate_group="e-1",
                        cluster="c-1",
                        drafts={"policy": {"file": CHECK_CASE_POLICY}},
                    ),
                    _case_unit(
                        "u-a2",
                        "A",
                        duplicate_group="e-2",
                        cluster="c-1",
                        group="qsPea:qsTea:qsPea",
                        codepoints="E650:E652:E650",
                        secondary_junctions=[{"primary_unit": "u-a1"}],
                    ),
                    _case_unit("u-a3", "A", ink_identical=True),
                ],
                [
                    _case_unit(
                        "u-a4",
                        "A",
                        duplicate_group="e-3",
                        cluster="c-2",
                        group="qsTea",
                        codepoints="E652",
                        pair=None,
                        configs=["ss03"],
                        config_gate=[{"feature": "ss03"}],
                    ),
                    _case_unit(
                        "u-a5",
                        "A",
                        duplicate_group="e-4",
                        cluster="c-1",
                        group="qsTea:qsPea",
                        codepoints="E652:E650",
                        secondary_junctions=[{"primary_unit": None}],
                    ),
                ],
            ],
        ),
        (
            "B",
            True,
            [
                [
                    _case_unit("u-b1", "B", no_verdict=True),
                    _case_unit("u-b2", "B", no_verdict=True, picture_identical=True),
                ]
            ],
        ),
        (
            "C",
            False,
            [
                [
                    _case_unit(
                        "u-c1", "C", duplicate_group="e-5", cluster="c-3", group="qsBay", codepoints="E651"
                    )
                ]
            ],
        ),
    ]
    rank = audit.family_ranks(dict(LETTERS))
    index = {class_id: place for place, (class_id, _no_verdict, _chunks) in enumerate(classes)}
    units = [unit for _class_id, _no_verdict, chunks in classes for chunk in chunks for unit in chunk]
    order = [
        unit["id"]
        for unit in sorted(
            (unit for unit in units if not audit.slim_fragment(unit)),
            key=lambda unit: audit.triage_key(
                index[unit["class"]],
                unit["group"],
                audit.parse_codepoints(unit["codepoints"]),
                unit["id"],
                rank,
            ),
        )
    ]
    place = {unit_id: position for position, unit_id in enumerate(order)}
    metas = []
    fed = []
    for class_id, no_verdict, chunks in classes:
        members = [unit for chunk in chunks for unit in chunk]
        checks = {check: sum(unit.get(check) is True for unit in members) for check in audit.MACHINE_CHECKS}
        meta = {
            "id": class_id,
            "no_verdict": no_verdict,
            "unit_count": len(members),
            "row_count": 2 * len(members),
            "machine_approved_count": sum(checks.values()),
            "machine_checks": checks,
            "batches": sorted(
                {place[unit["id"]] // CHECK_CASE_BATCH for unit in members if unit["id"] in place}
            ),
        }
        metas.append(meta)
        for number, chunk in enumerate(chunks):
            fed.append(
                {
                    "meta": meta if number == 0 else None,
                    "units": [[unit, True] for unit in chunk],
                    "end": number == len(chunks) - 1,
                }
            )
    junctioned = [unit for unit in units if unit["secondary_junctions"]]
    links = [link for unit in junctioned for link in unit["secondary_junctions"]]
    manifest = {
        "totals": {
            "units": len(units),
            "rows": sum(meta["row_count"] for meta in metas),
            "batches": -(-len(order) // CHECK_CASE_BATCH),
        },
        "human_unit_ids": order,
        "machine_approved": {
            "units": sum(meta["machine_approved_count"] for meta in metas),
            "by_class": {
                meta["id"]: meta["machine_approved_count"] for meta in metas if meta["machine_approved_count"]
            },
        },
        "secondary_junctions": {
            "units_with_markers": len(junctioned),
            "junctions_with_primary_unit": sum(link["primary_unit"] is not None for link in links),
            "junctions_without_primary_unit": sum(link["primary_unit"] is None for link in links),
        },
        "classes": metas,
    }
    settings = {
        "descriptions": {"ss03": "gloss"},
        "batch_size": CHECK_CASE_BATCH,
        "repo_root": str(REPO_ROOT),
        "identity_for": ["u-a1"],
    }
    return _CheckCase(settings, fed, manifest)


def _check_cases() -> Iterator[tuple[str, _CheckCase]]:
    """The `corpus-check` stage's generated cases, each the base (`_check_base`) with one predicate broken: every per-unit, class-level and cross-unit predicate the write's checkers run, each made to fail, to pass a value Python reads as equal, and to raise where Python raises, with repeats and two-cluster groups inside one chunk and across chunks and classes."""
    base = _check_base()

    def case(name: str, *changes: Callable[[_CheckCase], object]) -> tuple[str, _CheckCase]:
        mutated = copy.deepcopy(base)
        for change in changes:
            change(mutated)
        return name, mutated

    def unit(unit_id: str, **fields: object) -> Callable[[_CheckCase], object]:
        return lambda held: held.set(unit_id, **fields)

    def meta(class_id: str, **fields: object) -> Callable[[_CheckCase], object]:
        return lambda held: held.meta(class_id).update(fields)

    def setting(**fields: object) -> Callable[[_CheckCase], object]:
        return lambda held: held.settings.update(fields)

    def totals(**fields: object) -> Callable[[_CheckCase], object]:
        return lambda held: held.manifest["totals"].update(fields)

    def manifest(**fields: object) -> Callable[[_CheckCase], object]:
        return lambda held: held.manifest.update(fields)

    def dropped(class_id: str, key: str) -> Callable[[_CheckCase], object]:
        return lambda held: held.meta(class_id).pop(key)

    def chunk(index: int, **fields: object) -> Callable[[_CheckCase], object]:
        return lambda held: held.chunks[index].update(fields)

    def replaced(unit_id: str, value: object, cached: bool = True) -> Callable[[_CheckCase], object]:
        def change(held: _CheckCase) -> None:
            held.fed(unit_id)[:] = [value, cached]

        return change

    def uncached(*unit_ids: str) -> Callable[[_CheckCase], object]:
        def change(held: _CheckCase) -> None:
            for unit_id in unit_ids:
                held.fed(unit_id)[1] = False

        return change

    missing = "glyph_data/runes/qsMissing.yaml"
    yield case("base")
    yield case("identity-for-none", setting(identity_for=None))
    yield case("identity-for-empty", setting(identity_for=[]))
    yield case("no-verdict-class-batches", meta("B", batches=[0]))
    yield case("no-verdict-class-one", meta("B", no_verdict=1))
    for value in (False, 1, None):
        yield case(f"no-verdict-unit:{value}", unit("u-b2", no_verdict=value))
    yield case("class-mismatch", unit("u-c1", **{"class": "A"}))
    yield case("duplicate-id-in-chunk", unit("u-a2", id="u-a1"))
    yield case("duplicate-id-across-chunks", unit("u-a4", id="u-a1"))
    yield case("duplicate-id-across-classes", unit("u-c1", id="u-a1"))
    yield case("duplicate-ids-sorted", unit("u-a4", id="u-a2"), unit("u-a5", id="u-a1"))
    yield case("duplicate-id-none", unit("u-b1", id=None), unit("u-b2", id=None))
    yield case("two-clusters-in-chunk", unit("u-a2", duplicate_group="e-1", cluster="c-9"))
    yield case("two-clusters-across-chunks", unit("u-a4", duplicate_group="e-1"))
    yield case(
        "two-clusters-first-kept",
        unit("u-a4", duplicate_group="e-1", cluster="c-9"),
        unit("u-a5", duplicate_group="e-1", cluster="c-9"),
    )
    yield case("group-spans-classes", unit("u-c1", duplicate_group="e-1", cluster="c-1"))
    yield case("cluster-spans-configs", unit("u-a4", cluster="c-1"))
    yield case("group-none-human", unit("u-c1", duplicate_group=None, cluster=None))
    yield case("keys-unorderable", unit("u-a1", **{"class": None}), unit("u-a2", duplicate_group="e-1"))
    yield case("keys-configs-str", unit("u-a1", configs="default"))
    yield case("keys-configs-dict", unit("u-a1", configs={"default": 1}))
    yield case("keys-configs-int", unit("u-a1", configs=5))
    yield case("keys-configs-nested", unit("u-a1", configs=[["default"]]))
    yield case("keys-class-list", unit("u-a1", **{"class": ["A"]}))
    yield case("keys-group-unhashable", unit("u-a1", duplicate_group=["e-1"]))
    yield case("config-gate-unglossed", unit("u-a4", config_gate=[{"feature": "ss99"}]))
    yield case("config-gate-empty-gloss", setting(descriptions={"ss03": ""}))
    yield case(
        "config-gate-mixed", unit("u-a4", config_gate=["ss03", None, {"feature": "ss03"}, {}, {"feature": 3}])
    )
    yield case("config-gate-str", unit("u-a4", config_gate="ss03"))
    yield case("config-gate-dict", unit("u-a4", config_gate={"feature": "ss99"}))
    yield case("config-gate-int", unit("u-a4", config_gate=5))
    yield case("config-gate-unhashable", unit("u-a4", config_gate=[{"feature": ["ss03"]}]))
    yield case("policy-missing", unit("u-a1", drafts={"policy": {"file": missing}}))
    yield case(
        "policy-two",
        unit("u-c1", drafts={"policy": {"file": missing}}),
        unit("u-a1", drafts={"policy": {"file": "glyph_data/runes/qsAnother.yaml"}}),
    )
    yield case("policy-empty-name", unit("u-a1", drafts={"policy": {"file": ""}}))
    yield case("policy-not-str", unit("u-a1", drafts={"policy": {"file": 5}}))
    yield case("policy-none", unit("u-a1", drafts={"policy": None}))
    yield case("policy-drafts-str", unit("u-a1", drafts="x"))
    yield case("policy-list", unit("u-a1", drafts={"policy": ["x"]}))
    yield case("policy-no-root", unit("u-a1", drafts={"policy": {"file": missing}}), setting(repo_root=None))
    yield case("unit-count", meta("C", unit_count=2))
    yield case("unit-count-no-id", meta("C", unit_count=2), dropped("C", "id"))
    yield case("machine-count", meta("A", machine_approved_count=0))
    yield case("machine-checks", meta("A", machine_checks=dict.fromkeys(audit.MACHINE_CHECKS, 0)))
    yield case(
        "machine-checks-reordered",
        meta("A", machine_checks={"junior_equivalent": 0, "picture_identical": 0, "ink_identical": 1}),
    )
    yield case("machine-checks-list", meta("A", machine_checks=[1]))
    yield case("machine-no-id", dropped("B", "id"))
    yield case("row-count-none", meta("C", row_count=None))
    yield case("row-count-dropped", dropped("C", "row_count"))
    yield case("row-count-bool", meta("C", row_count=True))
    yield case("no-class-start", chunk(2, meta=None))
    yield case("machine-before-class-start", chunk(0, meta=None))
    yield case("class-left-open", chunk(3, end=False))
    yield case("junction-self", unit("u-a2", secondary_junctions=[{"primary_unit": "u-a2"}]))
    yield case("junction-missing", unit("u-a2", secondary_junctions=[{"primary_unit": "u-zz"}]))
    yield case(
        "junction-not-substring",
        unit("u-a2", secondary_junctions=[{"primary_unit": "u-c1"}]),
        setting(identity_for=None),
    )
    yield case("junction-not-substring-unkept", unit("u-a2", secondary_junctions=[{"primary_unit": "u-c1"}]))
    yield case("junction-no-pair", unit("u-a1", pair=None))
    yield case("junction-primary-identical", unit("u-a1", ink_identical=True))
    yield case("junction-unit-identical", unit("u-a2", picture_identical=True))
    yield case("junction-codepoints-int", unit("u-a1", codepoints=5))
    yield case("junction-codepoints-none", unit("u-a1", codepoints=None))
    yield case("junction-entries", unit("u-a5", secondary_junctions=["x", None, {"primary_unit": None}, {}]))
    yield case("junction-str", unit("u-a5", secondary_junctions="ab"))
    yield case("junction-dict", unit("u-a5", secondary_junctions={"primary_unit": "u-a1"}))
    yield case("junction-int", unit("u-a5", secondary_junctions=5))
    yield case("junction-empty", unit("u-a5", secondary_junctions=[]))
    yield case("junction-unhashable", unit("u-a2", secondary_junctions=[{"primary_unit": ["u-a1"]}]))
    yield case(
        "junction-counts", lambda held: held.manifest["secondary_junctions"].update(units_with_markers=9)
    )
    yield case("junction-counts-none", manifest(secondary_junctions=None))
    yield case("junction-counts-list", manifest(secondary_junctions=[]))
    yield case("totals-units", totals(units=0))
    yield case("totals-rows", totals(rows=True))
    yield case("totals-dropped", lambda held: held.manifest.pop("totals"))
    yield case("totals-none", manifest(totals=None))
    yield case("manifest-list", lambda held: setattr(held, "manifest", []))
    yield case("index-swapped", lambda held: held.manifest["human_unit_ids"].reverse())
    yield case("index-missing", lambda held: held.manifest["human_unit_ids"].pop())
    yield case("index-extra", lambda held: held.manifest["human_unit_ids"].append("u-extra"))
    yield case("index-non-str", lambda held: held.manifest["human_unit_ids"].append(5))
    yield case(
        "index-tuple",
        lambda held: held.manifest.update(human_unit_ids=tuple(held.manifest["human_unit_ids"])),
    )
    yield case("index-none", manifest(human_unit_ids=None))
    yield case("machine-units", lambda held: held.manifest["machine_approved"].update(units=0))
    yield case("machine-none", manifest(machine_approved=None))
    yield case("machine-str", manifest(machine_approved="x"))
    yield case("by-class-empty", lambda held: held.manifest["machine_approved"].update(by_class={}))
    yield case("by-class-list", lambda held: held.manifest["machine_approved"].update(by_class=[["A", 1]]))
    yield case(
        "by-class-bool",
        lambda held: held.manifest["machine_approved"].update(by_class={"A": True, "B": True}),
    )
    for size in (0, True, None, 3, "2", -1):
        yield case(f"batch-size:{size!r}", setting(batch_size=size))
    yield case("class-batches", meta("A", batches=[7]))
    yield case("class-batches-tuple", meta("C", batches=(1,)))
    yield case("classes-reversed", lambda held: held.manifest["classes"].reverse())
    yield case("classes-none", manifest(classes=None))
    yield case(
        "classes-repeated", lambda held: held.manifest["classes"].append(copy.deepcopy(held.meta("A")))
    )
    yield case("classes-str", manifest(classes="A"))
    yield case("classes-int", manifest(classes=5))
    yield case("classes-int-index-none", manifest(classes=5, human_unit_ids=None))
    yield case("group-unranked", unit("u-c1", group="qsNope:qsBay"))
    yield case("codepoints-bad", unit("u-c1", codepoints="ZZ"))
    yield case("codepoints-forms", unit("u-c1", codepoints=" 0x0e6_51 "), unit("u-a4", codepoints="e652"))
    yield case("codepoints-negative", unit("u-c1", codepoints="-1"))
    yield case("unit-not-dict", replaced("u-c1", []))
    yield case("unit-not-dict-uncached", replaced("u-c1", [], cached=False))
    yield case("uncached", uncached("u-a1", "u-a2", "u-b1"))
    yield case("uncached-junction-int", uncached("u-c1"), unit("u-c1", secondary_junctions=5))
    for value in (None, 5, ("u-c1",), ["u-c1"]):
        yield case(f"id:{value!r}", unit("u-c1", id=value))


def _corpus_check_generated(_source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `corpus-check` stage's generated lines: each finish the build made, again over its chunks' facts with its manifest mutated (`_manifest_mutations`), under `generated:finish:`; and each case of `_check_cases`, run through the write's checkers over its own settings and chunks, under `generated:case:`, each valued as the build's lines are or `{"raised": name}`."""
    for number, (settings, keys, held) in enumerate(_CHECK_FINISHED, 1):
        if not isinstance(held, dict):
            continue
        for name, mutated in _manifest_mutations(held):
            value = _check_outcome(
                lambda mutated=mutated: _check_folded(
                    _write_checker(settings), (copy.deepcopy(_CHECK_CHUNKS[key]) for key in keys), mutated
                )
            )
            yield {
                "key": f"generated:finish:{number}:{name}",
                "chunks": keys,
                "manifest": mutated,
                "value": value,
            }
    outcome = _check_case_value(_check_base())
    if not isinstance(outcome, dict) or outcome.get("errors") != []:
        raise ValueError(f"the corpus-check stage's base case does not pass the check: {outcome!r:.400}")
    for name, held in _check_cases():
        yield {
            "key": f"generated:case:{name}",
            "settings": held.settings,
            "chunks": held.chunks,
            "manifest": held.manifest,
            "value": _check_case_value(held),
        }


def _check_case_value(held: _CheckCase) -> object:
    """Run one generated case as the write runs its checkers: one run checker fed each chunk in order, each chunk's facts handed over and folded into a fresh checker, finished over the case's manifest."""
    copied = copy.deepcopy(held)

    def check() -> object:
        run = _write_checker(copied.settings, copied.settings["identity_for"])
        taken = []
        for chunk in copied.chunks:
            if chunk["meta"] is not None:
                run.class_start(chunk["meta"])
            for unit, cached in chunk["units"]:
                run.unit(unit, cached=cached)
            if chunk["end"]:
                run.class_end()
            taken.append(run.take_facts())
        return _check_folded(_write_checker(copied.settings), taken, copied.manifest)

    return _check_outcome(check)


SIDECAR_LINES_STAGE = "sidecar-lines"
# The `sidecar-lines` stage's directory, beside its stage file, of the bytes its lines name by sha256: each fragment a run projects, as its typed encoding's compact text, each cached fragment's stored bytes, and each file of a generated run's or cursor's corpus.
SIDECAR_LINES_BLOBS = "sidecar-lines.blobs"
# The blob directory of the build whose `sidecar-lines` stage records, for the length of that build (`_sidecar_blobs_at`).
_SIDECAR_BLOBS_AT: list[Path] = []
_SIDECAR_INIT = inspect.signature(build._SidecarLines.__init__)
_SIDECAR_UNIT = inspect.signature(build._SidecarLines.unit)
_SIDECAR_CACHED = inspect.signature(build._SidecarLines.cached)
# The sidecar each of a run's writers holds the lines of, by the attribute `_SidecarLines` holds it under, and each cursor by the name a `take` event gives it.
_SIDECAR_WRITERS = {
    "_index": unit_index.INDEX_NAME,
    "_app": app_index.APP_INDEX_NAME,
    "_locator": app_index.LOCATOR_ROWS_NAME,
}
_SIDECAR_CURSORS = {"_index_cursor": "index", "_app_cursor": "app"}


@dataclass
class _SidecarFollowed:
    """One `build._SidecarLines` the stage follows: the prefix of its lines' keys, the events of the top-level call in progress (None between calls), how many top-level calls it has finished, and, for a run of the build's, each call's inputs, which the generated runs replay."""

    prefix: str
    events: list[tuple] | None = None
    count: int = 0
    copy_forward: bool = False
    calls: list[dict[str, object]] = field(default_factory=list)


# Each followed run by its `_SidecarLines`' id; the run each of its writers and cursors belongs to and the name it carries, by the object's id; the build's runs in the order they opened; and the prefix and corpus a generated replay names its run by, which the build leaves empty.
_SIDECAR_RUNS: dict[int, _SidecarFollowed] = {}
_SIDECAR_OWNERS: dict[int, tuple[_SidecarFollowed, str]] = {}
_SIDECAR_RECORDED: list[_SidecarFollowed] = []
_SIDECAR_PLACE: list[tuple[str, dict[str, str]]] = []


@contextlib.contextmanager
def _sidecar_blobs_at(directory: Path) -> Iterator[None]:
    _SIDECAR_BLOBS_AT.append(directory)
    try:
        yield
    finally:
        _SIDECAR_BLOBS_AT.pop()
        _SIDECAR_RUNS.clear()
        _SIDECAR_OWNERS.clear()


def _sidecar_digest(data: bytes) -> tuple[int, str]:
    return len(data), hashlib.sha256(data).hexdigest()


def _sidecar_forget(lines: build._SidecarLines) -> None:
    """Stop following `lines`, its writers and its cursors."""
    _SIDECAR_RUNS.pop(id(lines), None)
    for attribute in (*_SIDECAR_WRITERS, *_SIDECAR_CURSORS):
        _SIDECAR_OWNERS.pop(id(getattr(lines, attribute, None)), None)


def _sidecars_opened(args: tuple, kwargs: dict) -> Finisher:
    """`build._SidecarLines.__init__`: an `open` line under the run's prefix, `run:<n>` for the build's runs, with whether the run copies the previous sidecars forward and the corpus a generated run's cursors read (None for the build's, whose cursors read the dump's prior corpus), valued whether each cursor, the units index's and the app index's, opened to lines."""
    bound = _SIDECAR_INIT.bind(*args, **kwargs)
    bound.apply_defaults()
    lines = bound.arguments["self"]
    copy_forward = bool(bound.arguments["copy_forward"])

    def finish(_result: object) -> Mapping[str, object]:
        if _SIDECAR_PLACE:
            prefix, held = _SIDECAR_PLACE[-1]
            corpus: dict[str, str] | None = held
            run = _SidecarFollowed(prefix, copy_forward=copy_forward)
        else:
            run = _SidecarFollowed(f"run:{len(_SIDECAR_RECORDED) + 1}", copy_forward=copy_forward)
            corpus = None
            _SIDECAR_RECORDED.append(run)
        _SIDECAR_RUNS[id(lines)] = run
        for attribute, name in (*_SIDECAR_WRITERS.items(), *_SIDECAR_CURSORS.items()):
            held_by = getattr(lines, attribute)
            if held_by is not None:
                _SIDECAR_OWNERS[id(held_by)] = (run, name)
        cursors = tuple(
            getattr(lines, attribute) is not None and getattr(lines, attribute)._lines is not None
            for attribute in _SIDECAR_CURSORS
        )
        return {"key": f"{run.prefix}:open", "copy_forward": copy_forward, "corpus": corpus, "value": cursors}

    return finish


def _sidecar_call(run: _SidecarFollowed, lines: build._SidecarLines, inputs: dict[str, object]) -> Finisher:
    run.events = []

    def finish(_result: object) -> Mapping[str, object]:
        events, run.events = run.events, None
        run.count += 1
        if not _SIDECAR_PLACE:
            run.calls.append(inputs)
        return {
            "key": f"{run.prefix}:call:{run.count}",
            **inputs,
            "value": (events, (lines.human, lines.machine, lines.copied_forward)),
        }

    return finish


def _sidecar_fragment_blob(fragment: object) -> str:
    return _blob_into(_SIDECAR_BLOBS_AT[-1], compact(encode(fragment)).encode("ascii"))


def _sidecar_unit(args: tuple, kwargs: dict) -> Finisher | None:
    """`build._SidecarLines.unit` called by the write, not from `cached`'s fallback, whose events belong to the `cached` call: a `call:<n>` line with the fragment, kept in the blob directory as its typed encoding (`_sidecar_fragment_blob`), its span and its place, valued the events the call made, each cursor's `take` with what it returned and each line written with the sidecar it went to, as their lengths and sha256s, and the run's counts after it."""
    bound = _SIDECAR_UNIT.bind(*args, **kwargs)
    lines = bound.arguments["self"]
    run = _SIDECAR_RUNS.get(id(lines))
    if run is None or run.events is not None:
        return None
    inputs: dict[str, object] = {
        "call": "unit",
        "fragment": _sidecar_fragment_blob(bound.arguments["fragment"]),
        "span": tuple(bound.arguments["span"]),
        "order": bound.arguments["order"],
        "batch": bound.arguments["batch"],
    }
    return _sidecar_call(run, lines, inputs)


def _sidecar_cached(args: tuple, kwargs: dict) -> Finisher | None:
    """`build._SidecarLines.cached`: a `call:<n>` line with the emission's unit id, its stored bytes, kept in the blob directory, the inflated length its source records and the identity that stands in for the fragment, its span and its place, valued as `_sidecar_unit`'s lines are."""
    bound = _SIDECAR_CACHED.bind(*args, **kwargs)
    lines = bound.arguments["self"]
    run = _SIDECAR_RUNS.get(id(lines))
    if run is None:
        return None
    emission: build._Emission = bound.arguments["emission"]
    assert emission.body is not None and emission.source is not None
    inputs: dict[str, object] = {
        "call": "cached",
        "unit_id": emission.unit_id,
        "body": _blob_into(_SIDECAR_BLOBS_AT[-1], emission.body),
        "inflated": emission.source.inflated,
        "identity": copy.deepcopy(emission.identity),
        "span": tuple(bound.arguments["span"]),
        "order": bound.arguments["order"],
        "batch": bound.arguments["batch"],
    }
    return _sidecar_call(run, lines, inputs)


def _sidecar_event(owner: object, event: Callable[[str], tuple]) -> None:
    held = _SIDECAR_OWNERS.get(id(owner))
    if held is not None and held[0].events is not None:
        held[0].events.append(event(held[1]))


def _sidecar_gzip_write(args: tuple, _kwargs: dict) -> None:
    _sidecar_event(args[0], lambda name: ("write", name, *_sidecar_digest(bytes(args[1]))))


def _sidecar_locator_add(args: tuple, _kwargs: dict) -> None:
    _sidecar_event(args[0], lambda name: ("write", name, *_sidecar_digest(bytes(args[1]))))


def _sidecar_take(args: tuple, _kwargs: dict) -> Finisher | None:
    cursor, value = args[0], args[1]
    if id(cursor) not in _SIDECAR_OWNERS:
        return None

    def finish(line: object) -> None:
        taken = None if line is None else _sidecar_digest(line)  # pyright: ignore[reportArgumentType]
        _sidecar_event(cursor, lambda name: ("take", name, value, taken))

    return finish


def _sidecars_closed(args: tuple, _kwargs: dict) -> Finisher | None:
    """`build._SidecarLines.close`: a `close` line valued the run's counts, its locator blocks' table entries, and the length and sha256 of its index lines and app-index lines as they inflate and of its locator rows as the gzip members they are; the compressed size the Python run holds them at (`held`) is not compared, since the Rust build holds them on disk."""
    lines = args[0]
    run = _SIDECAR_RUNS.get(id(lines))
    if run is None:
        return None

    def finish(result: object) -> Mapping[str, object]:
        closed, held = result  # pyright: ignore[reportGeneralTypeIssues]
        _sidecar_forget(lines)
        return {
            "key": f"{run.prefix}:close",
            "value": {
                "human": closed.human,
                "machine": closed.machine,
                "copied_forward": closed.copied_forward,
                "blocks": closed.blocks,
                "index": _sidecar_digest(gzip.decompress(held[unit_index.INDEX_NAME])),
                "app": _sidecar_digest(gzip.decompress(held[app_index.APP_INDEX_NAME])),
                "rows": _sidecar_digest(held[app_index.LOCATOR_ROWS_NAME]),
            },
        }

    return finish


class _SidecarCollector(_Sink):
    """What a generated replay's wrapped calls record, kept in order rather than written."""

    def __init__(self) -> None:
        self.lines: list[Mapping[str, object]] = []

    def record(
        self,
        stage: str,
        recorded: Mapping[str, object] | list[Mapping[str, object]] | Boundary | WalkedBoundary,
    ) -> None:
        if isinstance(recorded, Boundary | WalkedBoundary):
            raise TypeError(f"the {stage} stage records call lines, not {recorded!r:.80}")
        self.lines.extend(recorded if isinstance(recorded, list) else [recorded])


def _sidecar_corpus(directory: Path, blobs: Path) -> dict[str, str]:
    """Every file under `directory`, by its path there, kept in `blobs` under its sha256."""
    return {
        path.relative_to(directory).as_posix(): _blob_into(blobs, path.read_bytes())
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _sidecar_replayed(lines: build._SidecarLines, inputs: Mapping[str, Any], blobs: Path) -> None:
    """Make one recorded top-level call on `lines` again, its fragment or stored bytes read back from `blobs`."""
    span, order, batch = tuple(inputs["span"]), inputs["order"], inputs["batch"]
    if inputs["call"] == "unit":
        fragment = decode(json.loads((blobs / inputs["fragment"]).read_text(encoding="ascii")))
        lines.unit(fragment, span, order, batch)  # pyright: ignore[reportArgumentType]
        return
    unit_id = inputs["unit_id"]
    source = unit_cache.PriorFragment("", 0, 0, unit_id, None, True, inputs["inflated"])
    emission = build._Emission(
        unit_id,
        "",
        None,
        body=(blobs / inputs["body"]).read_bytes(),
        source=source,
        identity=copy.deepcopy(inputs["identity"]),
    )
    lines.cached(emission, span, order, batch)  # pyright: ignore[reportArgumentType]


def _sidecar_replay(
    prefix: str, directory: Path, copy_forward: bool, calls: Sequence[Mapping[str, Any]], blobs: Path
) -> list[Mapping[str, object]]:
    """Run `calls` through a `build._SidecarLines` over `directory` with the stage's recorders wrapped, as the build's run made them, and return the lines they record under `prefix`, with the files of `directory` kept in `blobs` as the run's corpus. A call that raises ends the run with a line valued `{"raised": name}`."""
    collector = _SidecarCollector()
    _SIDECAR_PLACE.append((prefix, _sidecar_corpus(directory, blobs)))
    try:
        with _sidecar_blobs_at(blobs), wrapped([BUILD_STAGES[SIDECAR_LINES_STAGE]], collector, BUILD_HOOKS):
            lines = build._SidecarLines(directory, copy_forward=copy_forward)
            try:
                for number, inputs in enumerate(calls, 1):
                    try:
                        _sidecar_replayed(lines, inputs, blobs)
                    except Exception as error:
                        raised = {"raised": type(error).__name__}
                        collector.lines.append({"key": f"{prefix}:call:{number}", **inputs, "value": raised})
                        return collector.lines
                lines.close()
            finally:
                lines.release()
                _sidecar_forget(lines)
    finally:
        _SIDECAR_PLACE.pop()
    return collector.lines


def _gzipped_lines(lines: Iterable[bytes]) -> bytes:
    return gzip.compress(b"".join(lines), compresslevel=1, mtime=0)


def _app_lines(prior: Path) -> list[bytes]:
    with gzip.open(app_index.artifact_path(prior, app_index.APP_INDEX_NAME), "rb") as stream:
        return list(stream)


def _index_files(prior: Path, directory: Path) -> None:
    for path in prior.glob(f"{unit_index.INDEX_NAME}*"):
        shutil.copyfile(path, directory / path.name)


def _materialized_lines(prior: Path) -> tuple[dict, list[bytes]]:
    """The prior units index's header and its record lines as `unit_index.OpenIndex.lines` gives them, each with the place its part holds."""
    opened = unit_index.open_index(prior)
    assert opened is not None, prior
    with opened:
        return opened.header, list(opened.lines())


def _sidecar_variants(
    prior: Path, run: _SidecarFollowed
) -> Iterator[tuple[str, bool, Callable[[Path], None]]]:
    """The generated corpora a run of the build's that copied lines forward is replayed over, each with whether the replay copies forward and how its directory is filled: no copy-forward; no units index; no app index; a single-file index lacking a middle cached unit's line; an app index lacking a middle cached human unit's row; an index under a head with a segment that adds a line after the first cached unit's and drops the last cached unit's; and a single-file index cut short."""
    cached = [inputs for inputs in run.calls if inputs["call"] == "cached"]
    human = [inputs for inputs in cached if inputs["order"] is not None and inputs["batch"] is not None]
    app = _app_lines(prior)
    header, index = _materialized_lines(prior)
    head_line = (json.dumps(header) + "\n").encode()

    def lines_without(lines: list[bytes], unit_id: object) -> list[bytes]:
        prefix = f'{{"id": {json.dumps(unit_id)}, '.encode()
        return [line for line in lines if not line.startswith(prefix)]

    def with_app(directory: Path) -> None:
        app_index.artifact_path(directory, app_index.APP_INDEX_NAME).write_bytes(_gzipped_lines(app))

    yield "no-copy-forward", False, lambda _directory: None

    def index_missing(directory: Path) -> None:
        with_app(directory)

    yield "index-missing", True, index_missing
    yield "app-missing", True, lambda directory: _index_files(prior, directory)
    middle = cached[len(cached) // 2]["unit_id"]

    def index_gap(directory: Path) -> None:
        unit_index.index_path(directory).write_bytes(
            _gzipped_lines([head_line, *lines_without(index, middle)])
        )
        with_app(directory)

    yield "index-gap", True, index_gap
    if human:
        missing = human[len(human) // 2]["unit_id"]

        def app_gap(directory: Path) -> None:
            _index_files(prior, directory)
            app_index.artifact_path(directory, app_index.APP_INDEX_NAME).write_bytes(
                _gzipped_lines([app[0], *lines_without(app[1:], missing)])
            )

        yield "app-gap", True, app_gap

    def index_segment(directory: Path) -> None:
        _index_files(prior, directory)
        shutil.copyfile(prior / "manifest.json", directory / "manifest.json")
        first, last = cached[0]["unit_id"], cached[-1]["unit_id"]
        changed: list[bytes] = []
        for line in lines_without(index, last):
            changed.append(line)
            if line.startswith(f'{{"id": {json.dumps(first)}, '.encode()):
                changed.append(line.replace(json.dumps(first).encode(), json.dumps(f"{first}0").encode(), 1))
        unit_index.write_index_lines(directory, changed)
        (directory / "manifest.json").unlink()
        with_app(directory)

    yield "index-segment", True, index_segment

    def index_truncated(directory: Path) -> None:
        whole = _gzipped_lines([head_line, *index])
        unit_index.index_path(directory).write_bytes(whole[: len(whole) * 2 // 3])
        with_app(directory)

    yield "index-truncated", True, index_truncated


def _sidecar_runs_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """Each run of the build's that copied a cached unit's lines forward, replayed over each corpus of `_sidecar_variants` (`_sidecar_replay`), its lines keyed `generated:run:<variant>:<n>`."""
    blobs = source.out / SIDECAR_LINES_BLOBS
    prior = source.out / DUMP_PRIOR
    for number, run in enumerate(_SIDECAR_RECORDED, 1):
        if not run.copy_forward or not any(inputs["call"] == "cached" for inputs in run.calls):
            continue
        for name, copy_forward, fill in _sidecar_variants(prior, run):
            with tempfile.TemporaryDirectory(prefix="sidecar-lines-stage-") as scratch:
                directory = Path(scratch)
                fill(directory)
                yield from _sidecar_replay(
                    f"generated:run:{name}:{number}", directory, copy_forward, run.calls, blobs
                )


def _sidecar_outcome(call: Callable[[], bytes]) -> object:
    try:
        return call()
    except Exception as error:
        return {"raised": type(error).__name__}


# The fragment the `sidecar-lines` stage's generated projections change one field of at a time.
SIDECAR_FRAGMENT: dict[str, object] = {
    "id": "u-1",
    "class": "A",
    "ledger_class": "A",
    "group": "qsPea:qsTea",
    "duplicate_group": "e-1",
    "cluster": "c-1",
    "notation": "·Pea ~b~ ·Tea",
    "notation_tokens": ["·Pea", "~b~", "·Tea"],
    "codepoints": "E650:E652",
    "pair": {"left": 0, "right": 1},
    "pair_codepoints": "E650:E652",
    "boundary_marks": [{"at": 1}],
    "secondary_junctions": [{"primary_unit": "u-2"}],
    "configs": ["default", "ss03"],
    "config_gate": "ss03",
    "config_note": "only ss03",
    "config_classes": {"default": "A"},
    "config_class_note": None,
    "render_groups": [{"configs": ["default"]}, {"configs": ["ss03"]}],
    "summary": "a summary",
    "exemplar": True,
    "kinds": ["join"],
    "ink_identical": False,
    "picture_identical": False,
    "junior_equivalent": False,
    "ink_deltas": {"default": "d-1"},
    "no_verdict": False,
    "content_key": "k-1",
    "provenance": [{"rune": "qsPea"}],
    "before": {"glyphs": ["qsPea"], "junctions": [1]},
    "after": {"cells": [[0, 1]], "junctions": []},
    "drafts": {"policy": {"file": "runes/qsPea.yaml", "keypath": "a.b", "suggested_record": {"x": 1}}},
}
# The values the generated projections put in a field, which reach `or []`, `len`, `.get` and subscripting with each kind of value.
SIDECAR_VALUES: tuple[object, ...] = (
    None,
    False,
    True,
    0,
    5,
    "",
    "abc",
    '·Pea ü 😀   \x00\x1f\x7f "\\',
    [],
    [1, "x"],
    (),
    {},
    {"a": 1, "b": None},
    {"glyphs": "g", "cells": None, "junctions": [2]},
)


def _sidecar_fragments() -> Iterator[tuple[str, dict[str, object]]]:
    """Each generated projection's fragment: `SIDECAR_FRAGMENT`, with each field dropped, set to each of `SIDECAR_VALUES`, and the policy draft given every shape."""
    yield "base", dict(SIDECAR_FRAGMENT)
    for name in SIDECAR_FRAGMENT:
        yield f"drop:{name}", {key: value for key, value in SIDECAR_FRAGMENT.items() if key != name}
        for index, value in enumerate(SIDECAR_VALUES):
            yield f"set:{name}:{index}", {**SIDECAR_FRAGMENT, name: value}
    for index, policy in enumerate(
        (
            None,
            {},
            {"file": "f"},
            {"keypath": "k"},
            {"file": "f", "keypath": "k"},
            {"file": None, "keypath": 3, "suggested_record": [1]},
            ["file"],
            "policy",
            7,
        )
    ):
        yield f"policy:{index}", {**SIDECAR_FRAGMENT, "drafts": {"policy": policy}}
    yield "id-unicode", {**SIDECAR_FRAGMENT, "id": "u-ü"}


# The places and spans the generated projections take each fragment at.
SIDECAR_PLACES: tuple[tuple[int | None, int | None, tuple[int, int, int, int]], ...] = (
    (3, 1, (0, 10, 20, 0)),
    (None, None, (2, 0, 7, 0)),
    (0, None, (1, 5, 9, 40)),
    (None, 2, (0, 1, 1, 1)),
)
# The unit id the generated copy-forwards give each line they rewrite.
SIDECAR_COPY_ID = "u-9"


def _sidecar_lines_of(
    fragment: dict[str, object], order: int | None, batch: int | None, span: tuple[int, int, int, int]
) -> dict[str, object]:
    part, start, length, inflated = span
    deflated = bool(inflated)
    return {
        "index": _sidecar_outcome(lambda: unit_index.index_line(fragment, order=order, batch=batch)),
        "app": _sidecar_outcome(
            lambda: app_index.app_line(
                fragment,
                part,
                start,
                length,
                order=order,  # pyright: ignore[reportArgumentType]
                batch=batch,  # pyright: ignore[reportArgumentType]
                deflated=deflated,
            )
        ),
        "locator": _sidecar_outcome(
            lambda: app_index.locator_line(fragment, part, start, length, deflated=deflated)
        ),
    }


def _sidecar_copy_lines(source_lines: Sequence[bytes]) -> Iterator[tuple[str, bytes]]:
    """The lines the generated copy-forwards rewrite: the first index line and app-index row the build's cursors took, and lines made to reach every seam's edge."""
    for index, line in enumerate(source_lines):
        yield f"taken:{index}", line
    row = app_index.app_line(dict(SIDECAR_FRAGMENT), 1, 2, 3, order=4, batch=5)
    yield "index-base", unit_index.index_line(dict(SIDECAR_FRAGMENT), order=4, batch=0)
    yield "index-segment", unit_index._cut(unit_index.index_line(dict(SIDECAR_FRAGMENT)))
    yield "index-no-class", b'{"id": "u-1", "order": 1}\n'
    yield "app-row", row
    yield "app-deflated", app_index.app_line(dict(SIDECAR_FRAGMENT), 1, 2, 3, order=4, batch=5, deflated=True)
    yield "app-no-shard-part", row.replace(b', "shard_part": ', b', "shard": ')
    yield "app-shard-part-first", b'{"id": "u-1", "shard_part": 1, "x": 2, "class": "A"}\n'
    yield "app-shard-part-twice", row[:-2] + b', "shard_part": 9}\n'


def _sidecar_cursor_cases(prior: Path) -> Iterator[tuple[str, str, dict[str, bytes], list[str]]]:
    """The generated cursors, each a sidecar it reads, `app` or `index`, the files of its corpus and the ids taken in order: app indexes missing, empty, not gzip, header-only, with rows taken in order, skipped, out of order and missed, with a prefix collision and a non-ASCII id, cut short and followed by bytes that are not gzip; units indexes over the dump's prior corpus, as a single file, under a head missing its part, and absent."""
    app_name = app_index.APP_INDEX_NAME
    head = b'{"format": "x"}\n'
    rows = [f'{{"id": "u-{number}", "class": "A"}}\n'.encode() for number in range(1, 400)]
    yield "app-missing", "app", {}, ["u-1"]
    yield "app-empty", "app", {app_name: b""}, ["u-1"]
    yield "app-not-gzip", "app", {app_name: b"hello\n"}, ["u-1"]
    yield "app-header", "app", {app_name: _gzipped_lines([head])}, ["u-1", "u-2"]
    yield "app-rows", "app", {app_name: _gzipped_lines([head, *rows])}, ["u-1", "u-3", "u-2", "u-5"]
    yield "app-missed", "app", {app_name: _gzipped_lines([head, *rows])}, ["u-2", "u-0", "u-3"]
    yield "app-header-no-newline", "app", {app_name: _gzipped_lines([b'{"format": "x"}'])}, ["u-1"]
    collide = [b'{"id": "u-1x", "class": "A"}\n', b'{"id": "u-1", "class": "A"}\n']
    yield "app-prefix", "app", {app_name: _gzipped_lines([head, *collide])}, ["u-1", "u-1x"]
    unicode = [b'{"id": "u-\xc3\xbc", "class": "A"}\n', rows[0]]
    yield "app-unicode", "app", {app_name: _gzipped_lines([head, *unicode])}, ["u-ü", "u-1"]
    whole = gzip.compress(b"".join([head, *rows]), compresslevel=6, mtime=0)
    yield "app-truncated", "app", {app_name: whole[: len(whole) // 2]}, ["u-1", "u-399"]
    yield "app-trailing", "app", {app_name: whole + b"not gzip"}, ["u-1", "u-399", "u-400"]
    index_files = {
        path.name: path.read_bytes()
        for path in sorted(prior.glob(f"{unit_index.INDEX_NAME}*"))
        if path.is_file()
    }
    header, index = _materialized_lines(prior) if unit_index.index_is_current(prior) else ({}, [])
    if index:
        ids = [unit_index.line_id(line) for line in index]
        taken = [*ids[:3], *ids[len(ids) // 2 : len(ids) // 2 + 2], "u-none", ids[-1]]
        yield "index-prior", "index", index_files, taken
        single = _gzipped_lines([(json.dumps(header) + "\n").encode(), *index])
        yield "index-single", "index", {unit_index.INDEX_NAME: single}, [ids[0], ids[-1]]
        headless = {name: data for name, data in index_files.items() if not name.endswith(".head")}
        yield "index-headless", "index", headless, ids[:1]
        partless = {name: data for name, data in index_files.items() if name.endswith(".head")}
        yield "index-partless", "index", partless, ids[:1]
    yield "index-missing", "index", {}, ["u-1"]


def _sidecar_cursor_value(which: str, directory: Path, takes: Sequence[str]) -> dict[str, object]:
    cursor = (
        unit_index.LineCursor.over_index(directory)
        if which == "index"
        else unit_index.LineCursor(app_index.artifact_path(directory, app_index.APP_INDEX_NAME))
    )
    results: list[object] = []
    opened = cursor._lines is not None
    try:
        for value in takes:
            try:
                results.append(cursor.take(value))
            except Exception as error:
                results.append({"raised": type(error).__name__})
                break
    finally:
        cursor.close()
    return {"opened": opened, "results": results}


def _sidecar_locator_cases() -> Iterator[tuple[str, list[tuple[str, str | None]]]]:
    """The generated locator blocks' rows, each an id and a class: none; one class across several blocks and at a block's edges; classes that change, come back with lower ids and are None; and ids that repeat or fall within a block, across a block's edge and after a class change back."""
    ids = [f"u-{number:05d}" for number in range(2500)]
    rows = app_index.LOCATOR_BLOCK_ROWS
    yield "empty", []
    yield "one-class", [(unit_id, "A") for unit_id in ids]
    yield "block", [(unit_id, "A") for unit_id in ids[:rows]]
    yield "block-and-one", [(unit_id, "A") for unit_id in ids[: rows + 1]]
    yield "classes", [
        *((unit_id, "A") for unit_id in ids[10:13]),
        *((unit_id, "B") for unit_id in ids[:2]),
        *((unit_id, "A") for unit_id in ids[:2]),
        *((unit_id, None) for unit_id in ids[5:8]),
    ]
    yield "descending", [(ids[3], "A"), (ids[1], "A")]
    yield "repeated", [(ids[3], "A"), (ids[3], "A")]
    yield "descending-across-blocks", [*((unit_id, "A") for unit_id in ids[1 : rows + 1]), (ids[0], "A")]
    yield "descending-in-none", [(ids[2], None), (ids[1], None)]


def _sidecar_locator_value(rows: Sequence[tuple[str, str | None]]) -> object:
    held = io.BytesIO()
    blocks = app_index.LocatorBlocks(held)
    try:
        for index, (unit_id, class_id) in enumerate(rows):
            blocks.add(
                app_index.locator_line(
                    {"id": unit_id, "class": class_id},
                    index % 3,
                    index * 100,
                    50 + index,
                    deflated=index % 4 == 0,
                )
            )
        entries = blocks.close()
    except ValueError as error:
        return {"raised": type(error).__name__, "message": str(error)}
    return {"blocks": entries, "rows": _sidecar_digest(held.getvalue())}


def _sidecar_lines_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `sidecar-lines` stage's generated lines: the build's runs replayed over other corpora (`_sidecar_runs_generated`); each fragment of `_sidecar_fragments` projected at each of `SIDECAR_PLACES` (`generated:line:`), valued its index line, app-index row and locator row or `{"raised": name}`; each line of `_sidecar_copy_lines` copied forward at those places (`generated:copy:`), the units index's way and, at a place with an order and a batch, the app index's; each cursor of `_sidecar_cursor_cases` (`generated:cursor:`), valued whether it opened to lines and each take's line, or what it raised; and each row set of `_sidecar_locator_cases` cut into blocks (`generated:locator:`), valued the table entries and the rows' length and sha256, or the refusal and its message."""
    blobs = source.out / SIDECAR_LINES_BLOBS
    yield from _sidecar_runs_generated(source)
    for name, fragment in _sidecar_fragments():
        for place, (order, batch, span) in enumerate(SIDECAR_PLACES):
            yield {
                "key": f"generated:line:{name}:{place}",
                "fragment": fragment,
                "span": span,
                "order": order,
                "batch": batch,
                "value": _sidecar_lines_of(fragment, order, batch, span),
            }
    for name, line in _sidecar_copy_lines(_sidecar_taken_lines(source)):
        for place, (order, batch, span) in enumerate(SIDECAR_PLACES):
            part, start, length, inflated = span
            value: dict[str, object] = {
                "index": _sidecar_outcome(
                    lambda: unit_index.copy_forward_index_line(
                        line, unit_id=SIDECAR_COPY_ID, order=order, batch=batch
                    )
                )
            }
            if order is not None and batch is not None:
                value["app"] = _sidecar_outcome(
                    lambda: app_index.copy_forward_app_line(
                        line,
                        unit_id=SIDECAR_COPY_ID,
                        order=order,  # pyright: ignore[reportArgumentType]
                        batch=batch,  # pyright: ignore[reportArgumentType]
                        part=part,
                        start=start,
                        length=length,
                        deflated=bool(inflated),
                    )
                )
            yield {
                "key": f"generated:copy:{name}:{place}",
                "line": line,
                "unit_id": SIDECAR_COPY_ID,
                "span": span,
                "order": order,
                "batch": batch,
                "value": value,
            }
    for name, which, files, takes in _sidecar_cursor_cases(source.out / DUMP_PRIOR):
        with tempfile.TemporaryDirectory(prefix="sidecar-lines-cursor-") as scratch:
            directory = Path(scratch)
            for file_name, data in files.items():
                (directory / file_name).write_bytes(data)
            yield {
                "key": f"generated:cursor:{name}",
                "which": which,
                "corpus": {file_name: _blob_into(blobs, data) for file_name, data in files.items()},
                "takes": takes,
                "value": _sidecar_cursor_value(which, directory, takes),
            }
    for name, rows in _sidecar_locator_cases():
        yield {"key": f"generated:locator:{name}", "rows": rows, "value": _sidecar_locator_value(rows)}


def _sidecar_taken_lines(source: BuildSource) -> list[bytes]:
    """The first units-index line and the first app-index row of the dump's prior corpus, the lines the build's cursors take from, where it holds them."""
    prior = source.out / DUMP_PRIOR
    taken: list[bytes] = []
    if unit_index.index_is_current(prior):
        taken.extend(_materialized_lines(prior)[1][:1])
    if app_index.artifact_is_current(prior, app_index.APP_INDEX_NAME, app_index.APP_INDEX_FORMAT):
        taken.extend(_app_lines(prior)[1:2])
    return taken


WRITE_STAGE = "write"
# The keys of the `write` stage's lines beside its units: a line a class in shard order under its place, the files under the corpus's `units/` and `fonts/`, the fonts' records, the write's counters, its runs' sidecar lines taken together and the facts its checkers folded.
WRITE_CLASS_PREFIX = "class:"
WRITE_UNITS_FILES_KEY = "units-files"
WRITE_FONTS_KEY = "fonts"
WRITE_FONTS_FILES_KEY = "fonts-files"
WRITE_COUNTERS_KEY = "counters"
WRITE_SIDECARS_KEY = "sidecars"
WRITE_FACTS_KEY = "facts"
# The files a `generated:static:` case's directory holds before `build.copy_static` runs over it, with how often it runs: an empty directory, once and twice, and one holding an `index.html` with other bytes and a file the copy does not name.
_STATIC_CASES: tuple[tuple[dict[str, bytes], int], ...] = (
    ({}, 1),
    ({}, 2),
    ({"index.html": b"<p>other</p>\n", "extra.txt": b"kept\n"}, 1),
)
# The `generated:font:` cases: the files the corpus holds before `build._copy_font` runs, whether the digest it checks against is the font's own, and how often it runs.
_FONT_CASES: tuple[tuple[dict[str, bytes], bool, int], ...] = (
    ({}, True, 1),
    ({}, True, 2),
    ({"fonts/before.otf": b"other bytes\n"}, True, 1),
    ({}, False, 1),
)


def _write_reading(store: UnitStore, ordinal: int) -> tuple[object, int, str | None, str | None]:
    """One unit as the `write` stage reads it from the store once the write has committed: its shard address, part, start and length, or None, the inflated length of a deflated fragment, and its config note and policy file."""
    return (
        store.written_address(ordinal),
        store.written_inflated(ordinal),
        store.config_note(ordinal),
        store.policy_file(ordinal),
    )


def _files_held(directory: Path) -> list[tuple[str, int, str]]:
    """Each regular file under `directory`, by its path relative to it in code-point order, with its size and sha256; none for a directory that is not there."""
    if not directory.is_dir():
        return []
    held: list[tuple[str, int, str]] = []
    for name, path in sorted(
        (path.relative_to(directory).as_posix(), path) for path in directory.rglob("*") if path.is_file()
    ):
        with path.open("rb") as handle:
            held.append((name, path.stat().st_size, hashlib.file_digest(handle, "sha256").hexdigest()))
    return held


def _write_sidecars_value(written: Mapping[int, build._RunWritten]) -> tuple:
    """The runs' sidecar lines taken together, in shard order: the app-index and locator row counts, the rows copied forward, the index lines', the app-index lines' and the locator rows' bytes run after run as their length and sha256, and the locator blocks' table entries, each `byte_start` moved by the rows of the runs before it, as `app_index.write_app_artifacts_lines` moves them."""
    digests = {name: hashlib.sha256() for name in (unit_index.INDEX_NAME, app_index.APP_INDEX_NAME)}
    sizes = dict.fromkeys(digests, 0)
    rows = hashlib.sha256()
    base = 0
    blocks: list[dict] = []
    for index in range(len(written)):
        run = written[index]
        for name, digest in digests.items():
            with gzip.GzipFile(fileobj=io.BytesIO(run.lines[name]), mode="rb") as stream:
                while chunk := stream.read(1 << 20):
                    digest.update(chunk)
                    sizes[name] += len(chunk)
        members = run.lines[app_index.LOCATOR_ROWS_NAME]
        blocks.extend({**entry, "byte_start": base + entry["byte_start"]} for entry in run.sidecars.blocks)
        rows.update(members)
        base += len(members)
    return (
        sum(run.sidecars.human for run in written.values()),
        sum(run.sidecars.machine for run in written.values()),
        sum(run.sidecars.copied_forward for run in written.values()),
        *((sizes[name], digest.hexdigest()) for name, digest in digests.items()),
        (base, rows.hexdigest()),
        blocks,
    )


def write_boundary(held: Mapping[str, Any]) -> Boundary:
    """The `write` boundary over `build._write_corpus`'s locals as the manifest is about to be written, every run's parts committed and the fonts copied (`held`): a unit as `_write_reading` reads it; a line a class in shard order, under its place there, with its id and manifest record, its `shards` filled; the files under the corpus's `units/` and `fonts/` (`_files_held`); the fonts' records; the write's counters, the classes folded and those written directly among them, as `_WrittenCorpus` takes them; the runs' sidecar lines taken together (`_write_sidecars_value`); and the facts the folding checker holds (`_check_facts`)."""
    table: UnitTable = held["table"]
    store: UnitStore = held["store"]
    to_write: Sequence[build._ClassToWrite] = held["to_write"]
    metas: Mapping[str, dict] = held["metas"]
    written: Mapping[int, build._RunWritten] = held["written"]
    out_dir = Path(held["out_dir"])
    counters = {
        "byte_copied": held["byte_copied"],
        "kept": held["kept"],
        "appended": held["appended"],
        "appended_inflated": held["appended_inflated"],
        "deflated": held["deflated"],
        "dead": held["dead"],
        "folded": sum(entry.route == build.FOLD for entry in to_write),
        "direct": sum(entry.route in (build.DIRECT, build.FIRST) for entry in to_write),
        "copied_forward": sum(run.sidecars.copied_forward for run in written.values()),
    }
    return Boundary(
        table.n,
        {"value": functools.partial(_write_reading, store)},
        [
            *(
                {"key": f"{WRITE_CLASS_PREFIX}{place}", "value": (entry.id, copy.deepcopy(metas[entry.id]))}
                for place, entry in enumerate(to_write)
            ),
            {"key": WRITE_UNITS_FILES_KEY, "value": _files_held(out_dir / "units")},
            {"key": WRITE_FONTS_KEY, "value": copy.deepcopy(held["fonts"])},
            {"key": WRITE_FONTS_FILES_KEY, "value": _files_held(out_dir / "fonts")},
            {"key": WRITE_COUNTERS_KEY, "value": counters},
            {"key": WRITE_SIDECARS_KEY, "value": _write_sidecars_value(written)},
            {"key": WRITE_FACTS_KEY, "value": _check_facts(held["check"])},
        ],
    )


def _manifest_writing(_args: tuple, _kwargs: dict) -> Finisher | None:
    """`build._write_json` as `build._write_corpus` calls it for the manifest, recorded as the `write` boundary over that frame's locals before the manifest is written (`write_boundary`); a call from anywhere else records nothing."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_code is not build._write_corpus.__code__:
        frame = frame.f_back
    if frame is None:
        return None
    boundary = write_boundary(frame.f_locals)
    return lambda _result: boundary


def _write_generated(source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `write` stage's generated lines: `build.copy_static` from the repository's static files into a directory holding each case of `_STATIC_CASES`, as often as the case says, under `generated:static:<n>`, valued what its last call returned, the counts lines its calls appended and the files the directory holds then (`_files_held`); and `build._copy_font` of the dump's clone of the before font into a corpus holding each case of `_FONT_CASES`, against the font's own digest or another, as often as the case says, under `generated:font:<n>`, valued the refusal's message or None, the counts lines and the files the corpus's `fonts/` holds then. Each case's input is the files held and the call's further arguments."""
    for index, (files, times) in enumerate(_STATIC_CASES):
        with tempfile.TemporaryDirectory(prefix="write-static-") as scratch:
            directory, counts = Path(scratch) / "out", Path(scratch) / "counts"
            directory.mkdir()
            counts.write_bytes(b"")
            for name, data in files.items():
                (directory / name).write_bytes(data)
            copied: list[str] = []
            with _counts_named(counts):
                for _ in range(times):
                    copied = build.copy_static(directory, build.STATIC_DIR)
            yield {
                "key": f"generated:static:{index}",
                "input": (files, times),
                "value": (copied, counts.read_bytes(), _files_held(directory)),
            }
    font = (source.out / DUMP_INPUTS / build.SITE_BEFORE_FONT.name).absolute()
    own = hashlib.sha256(font.read_bytes()).hexdigest()
    for index, (files, owned, times) in enumerate(_FONT_CASES):
        expected = own if owned else "0" * 64
        with tempfile.TemporaryDirectory(prefix="write-font-") as scratch:
            corpus, counts = Path(scratch) / "out", Path(scratch) / "counts"
            corpus.mkdir()
            counts.write_bytes(b"")
            for name, data in files.items():
                (corpus / name).parent.mkdir(parents=True, exist_ok=True)
                (corpus / name).write_bytes(data)
            refusal = None
            with _counts_named(counts):
                try:
                    for _ in range(times):
                        build._copy_font(font, corpus, "before.otf", "AMS Review Before", REPO_ROOT, expected)
                except SystemExit as stop:
                    refusal = str(stop.code)
            yield {
                "key": f"generated:font:{index}",
                "input": (files, str(font), "before.otf", "AMS Review Before", expected, times),
                "value": (refusal, counts.read_bytes(), _files_held(corpus / "fonts")),
            }


MANIFEST_STAGE = "manifest"
# The keys of the `manifest` stage's lines: the manifest's bytes as written, its identity, the shards pruned after it and the files under the corpus's `units/` then.
MANIFEST_BYTES_KEY = "manifest"
MANIFEST_IDENTITY_KEY = "identity"
MANIFEST_PRUNED_KEY = "pruned"
MANIFEST_UNITS_FILES_KEY = "units-files"
# The `generated:prune:` cases: the files a corpus holds before `build._prune_orphan_shards` runs over it, and the manifest's classes it runs with. They take no `units/`; kept parts, a part and a class the manifest no longer names, a part named only by its basename under another directory, a hidden part, a staged part, a file of another kind, a name `*.json` matches only case-insensitively, a directory and a nested file `*.json` matches; and a class in the single-`shard` form.
_PRUNE_CASES: tuple[tuple[dict[str, bytes], list[dict[str, object]]], ...] = (
    ({"other.txt": b"kept\n"}, [{"id": "a", "shards": ["units/a.json"]}]),
    (
        {
            "units/a.json": b"[]\n",
            "units/a.000.json": b"[]\n",
            "units/a.001.json": b"[]\n",
            "units/a.x000.json": b"[]\n",
            "units/b.json": b"[]\n",
            "units/gone.json": b"[]\n",
            "units/.json": b"[]\n",
            "units/a.x001.json.partial": b"[]\n",
            "units/notes.txt": b"kept\n",
            "units/UPPER.JSON": b"[]\n",
            "units/d.json/inner.json": b"[]\n",
            "units/sub/c.json": b"[]\n",
        },
        [
            {"id": "a", "shards": ["units/a.000.json", "units/a.x000.json"]},
            {"id": "b", "shards": ["elsewhere/b.json"]},
        ],
    ),
    ({"units/b.json": b"[]\n", "units/c.json": b"[]\n"}, [{"id": "b", "shard": "units/b.json"}]),
)


def _manifest_pruning(args: tuple, _kwargs: dict) -> Finisher | None:
    """`build._prune_orphan_shards` as `build._write_corpus` calls it once the manifest is written, recorded as the `manifest` boundary: the manifest's bytes as the build wrote them, its identity (`unit_index.manifest_identity`), the shards the call pruned and the files under the corpus's `units/` once it has (`_files_held`); a call from anywhere else records nothing."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_code is not build._write_corpus.__code__:
        frame = frame.f_back
    if frame is None:
        return None
    out_dir, manifest = Path(args[0]), args[1]
    written = (out_dir / "manifest.json").read_bytes()
    identity = unit_index.manifest_identity(manifest)

    def finish(pruned: object) -> Boundary:
        return Boundary(
            0,
            {},
            [
                {"key": MANIFEST_BYTES_KEY, "value": written},
                {"key": MANIFEST_IDENTITY_KEY, "value": identity},
                {"key": MANIFEST_PRUNED_KEY, "value": pruned},
                {"key": MANIFEST_UNITS_FILES_KEY, "value": _files_held(out_dir / "units")},
            ],
        )

    return finish


def _manifest_generated(_source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `manifest` stage's generated lines: `build._prune_orphan_shards` over a corpus holding each case of `_PRUNE_CASES`, with a manifest of the case's classes, under `generated:prune:<n>`, valued the names it pruned and the files the corpus holds then (`_files_held`). Each case's input is the files held and the classes."""
    for index, (files, classes) in enumerate(_PRUNE_CASES):
        with tempfile.TemporaryDirectory(prefix="manifest-prune-") as scratch:
            corpus = Path(scratch) / "out"
            corpus.mkdir()
            for name, data in files.items():
                (corpus / name).parent.mkdir(parents=True, exist_ok=True)
                (corpus / name).write_bytes(data)
            pruned = build._prune_orphan_shards(corpus, {"classes": classes})
            yield {
                "key": f"generated:prune:{index}",
                "input": (files, classes),
                "value": (pruned, _files_held(corpus)),
            }


INDEX_WRITE_STAGE = "index-write"
# The keys of the `index-write` stage's lines: the index's head as written, the files whose names start with the index's, what the write did and its note.
INDEX_WRITE_HEAD_KEY = "head"
INDEX_WRITE_FILES_KEY = "files"
INDEX_WRITE_OUTCOME_KEY = "write"
INDEX_WRITE_NOTE_KEY = "note"
# The units the `generated:` cases of the `index-write` stage write index lines for, each an id, a class and a summary: a class whose key sorts before another's only at its `.json` suffix, a class `json.loads` reads as no string and an id outside ASCII among them. The next sets change them: `_INDEX_UNITS_B` changes one summary, drops one unit, adds one and moves one to another class, `_INDEX_UNITS_C` changes one more summary, and `_INDEX_UNITS_F` changes every summary and adds units, so its segment's bytes pass the base's.
_IndexUnits = tuple[tuple[str, object, str], ...]
_INDEX_UNITS_A: _IndexUnits = (
    ("u1", "a", "one"),
    ("u2", "a", "two"),
    ("u3", "b", "three"),
    ("u4", "b", "four"),
    ("u6", "a-b", "six"),
    ("·Pea", "a", "pea"),
    ("u7", None, "none"),
)
_INDEX_UNITS_B: _IndexUnits = (
    ("u1", "a", "one"),
    ("u2", "a", "two, changed"),
    ("u4", "a-b", "four"),
    ("u5", "a", "five"),
    ("u6", "a-b", "six"),
    ("·Pea", "a", "pea"),
    ("u7", None, "none"),
)
_INDEX_UNITS_C: _IndexUnits = tuple(
    (unit_id, class_id, f"{summary}, again" if unit_id == "u1" else summary)
    for unit_id, class_id, summary in _INDEX_UNITS_B
)
_INDEX_UNITS_F: _IndexUnits = (
    *((unit_id, class_id, f"{summary}, wholly rewritten") for unit_id, class_id, summary in _INDEX_UNITS_A),
    *((f"w{number}", "c", f"added {number}") for number in range(8)),
)
# The manifests the cases' corpora hold, two that differ in a component of the identity and one that differs only in an asset component, which leaves the identity as it was.
_INDEX_MANIFESTS: tuple[dict[str, object], ...] = (
    {
        "format": "m",
        "human_unit_ids": ["u2", "u3"],
        "batch_size": 2,
        "inputs_fingerprint": {"code": "x", "static": "s"},
    },
    {
        "format": "m",
        "human_unit_ids": ["u1", "u2"],
        "batch_size": 2,
        "inputs_fingerprint": {"code": "y", "static": "s"},
    },
    {
        "format": "m",
        "human_unit_ids": ["u2", "u3"],
        "batch_size": 2,
        "inputs_fingerprint": {"code": "x", "static": "t"},
    },
)


def _index_write_files(corpus: Path) -> list[tuple[str, int, str]]:
    """Each regular file directly under `corpus` whose name starts with the units index's (`unit_index.INDEX_NAME`), its head, its parts, the single file and any staging file, by name in code-point order, with its size and sha256."""
    held: list[tuple[str, int, str]] = []
    for name in sorted(
        entry.name for entry in corpus.iterdir() if entry.name.startswith(unit_index.INDEX_NAME)
    ):
        path = corpus / name
        if path.is_file():
            with path.open("rb") as handle:
                held.append((name, path.stat().st_size, hashlib.file_digest(handle, "sha256").hexdigest()))
    return held


def _index_write_value(written: unit_index.IndexWrite) -> tuple[str, str, int, int]:
    """What one write of the index did, as `unit_index.IndexWrite` holds it: its outcome, a base's reason, and its rows and their bytes."""
    return (written.outcome, written.reason, written.rows, written.rows_bytes)


def _index_writing(args: tuple, _kwargs: dict) -> Finisher | None:
    """`unit_index.write_index_lines` as `build._write_sidecars` calls it once the manifest is on disk, recorded as the `index-write` boundary once it returns: the head it committed, the files whose names start with the index's (`_index_write_files`), what it did (`_index_write_value`) and its note; a call from anywhere else records nothing."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_code is not build._write_sidecars.__code__:
        frame = frame.f_back
    if frame is None:
        return None
    corpus = Path(args[0])

    def finish(written: object) -> Boundary:
        assert isinstance(written, unit_index.IndexWrite)
        return Boundary(
            0,
            {},
            [
                {
                    "key": INDEX_WRITE_HEAD_KEY,
                    "value": row_delta.head_path(corpus, unit_index.INDEX_NAME).read_bytes(),
                },
                {"key": INDEX_WRITE_FILES_KEY, "value": _index_write_files(corpus)},
                {"key": INDEX_WRITE_OUTCOME_KEY, "value": _index_write_value(written)},
                {"key": INDEX_WRITE_NOTE_KEY, "value": written.note()},
            ],
        )

    return finish


def _index_case_lines(
    units: Iterable[tuple[str, object, str]], manifest: Mapping[str, object]
) -> list[bytes]:
    """The index lines a build writes for `units` beside `manifest`, each with the place the manifest gives it (`unit_index.Places`), in key order (`unit_index.index_key`)."""
    places = unit_index.Places(manifest)
    lines = [
        unit_index.index_line(
            {"id": unit_id, "class": class_id, "cluster": "k", "summary": summary},
            **unit_index.human_unit_slot(places.positions, places.batch_size, {"id": unit_id}),
        )
        for unit_id, class_id, summary in units
    ]
    return sorted(lines, key=unit_index.index_key)


def _manifest_bytes(manifest: Mapping[str, object]) -> bytes:
    return (json.dumps(manifest, indent=1) + "\n").encode()


def _single_index_file(header: Mapping[str, object], lines: Sequence[bytes]) -> bytes:
    """The single file a corpus from before the segment format holds: its header line and its lines as one gzip stream."""
    return gzip.compress(
        (json.dumps(dict(header)) + "\n").encode() + b"".join(lines), compresslevel=1, mtime=0
    )


def _gzipped_part(data: bytes, name: str) -> bytes:
    """`data` as one gzip stream at level 1 with mtime 0 naming `name`, as `row_delta.write_part` writes a part."""
    buffer = io.BytesIO()
    with gzip.GzipFile(filename=name, fileobj=buffer, mode="wb", mtime=0, compresslevel=1) as stream:
        stream.write(data)
    return buffer.getvalue()


def _index_head(corpus: Path) -> dict:
    return json.loads(row_delta.head_path(corpus, unit_index.INDEX_NAME).read_bytes())


def _put_index_head(corpus: Path, document: Mapping[str, object]) -> None:
    row_delta.head_path(corpus, unit_index.INDEX_NAME).write_text(json.dumps(document, indent=1) + "\n")


def _index_case_edits(corpus: Path, edit: str) -> None:
    """Make one case's edit to a corpus whose index a write left: a head that does not parse, a head in another format, merging by place, a base cut short or another of its size, a segment whose header names another number, staging and unlisted files of the index's, the single file in place of the head and its parts, that file in another format, not gzipped or cut short, and a manifest that does not parse."""
    name = unit_index.INDEX_NAME
    head_path = row_delta.head_path(corpus, name)
    if edit == "head-unparsed":
        head_path.write_bytes(b"{not a head\n")
    elif edit == "head-format":
        document = _index_head(corpus)
        document["stamps"] = {**document["stamps"], "format": "ams-review-unit-index/0"}
        _put_index_head(corpus, document)
    elif edit == "head-merge":
        document = _index_head(corpus)
        del document["merge"]
        _put_index_head(corpus, document)
    elif edit in ("base-short", "base-changed"):
        base = corpus / _index_head(corpus)["base"]["name"]
        data = base.read_bytes()
        base.write_bytes(
            data[:-1]
            if edit == "base-short"
            else data[:-9] + bytes(byte ^ 0xFF for byte in data[-9:-8]) + data[-8:]
        )
    elif edit == "segment-header":
        document = _index_head(corpus)
        segment = document["segments"][0]
        path = corpus / segment["name"]
        with gzip.GzipFile(path, mode="rb") as stream:
            stream.readline()
            rows = stream.read()
        header = row_delta.segment_header(name, document["generation"], 2, row_delta.MERGE_KEY)
        path.write_bytes(_gzipped_part(header + rows, segment["name"]))
        segment["size"] = path.stat().st_size
        segment["sha256"] = row_delta.file_sha256(path)
        _put_index_head(corpus, document)
    elif edit == "staging":
        for leftover in (
            f"{name}.1.base.partial",
            f"{name}.head.partial",
            f"{name}.0.7.delta",
            f"{name}.9.base",
            f"{name}.x.base",
        ):
            (corpus / leftover).write_bytes(b"left\n")
    elif edit.startswith("single"):
        for path in corpus.iterdir():
            if path.name.startswith(name):
                path.unlink()
        header = {"format": unit_index.INDEX_FORMAT, "manifest_sha256": "0" * 64}
        if edit == "single-format":
            header["format"] = "ams-review-unit-index/0"
        data = _single_index_file(header, _index_case_lines(_INDEX_UNITS_A, _INDEX_MANIFESTS[0]))
        if edit == "single-not-gzip":
            data = b"not gzip\n"
        elif edit == "single-cut":
            data = data[: len(data) // 2]
        unit_index.index_path(corpus).write_bytes(data)
    elif edit == "manifest-unparsed":
        (corpus / "manifest.json").write_bytes(b"{not a manifest\n")
    else:
        raise ValueError(f"no index case edit {edit!r}")


# The `generated:` cases of the `index-write` stage: a name; the unit sets the prior passes wrote in turn, each through `unit_index.write_index_lines` beside the first manifest while the unit store holds the pass's fold, so no prior pass folds; the edits made to the corpus then (`_index_case_edits`); the place of the manifest in `_INDEX_MANIFESTS` the case's write runs beside; the units it writes, in key order unless the name ends in `unsorted`; and the output whose fold claim this process holds as the case's write runs, or None.
_INDEX_CASES: tuple[
    tuple[str, tuple[_IndexUnits, ...], tuple[str, ...], int, _IndexUnits, str | None], ...
] = (
    ("first", (), (), 0, _INDEX_UNITS_A, None),
    ("first-empty", (), (), 0, (), None),
    ("first-unsorted", (), (), 0, _INDEX_UNITS_A, None),
    ("head-alone", (_INDEX_UNITS_A,), (), 0, _INDEX_UNITS_A, None),
    ("head-restamped", (_INDEX_UNITS_A,), (), 1, _INDEX_UNITS_A, None),
    ("head-assets", (_INDEX_UNITS_A,), (), 2, _INDEX_UNITS_A, None),
    ("segment", (_INDEX_UNITS_A,), (), 1, _INDEX_UNITS_B, None),
    ("segment-second", (_INDEX_UNITS_A, _INDEX_UNITS_B), (), 1, _INDEX_UNITS_C, None),
    ("segment-tombstones", (_INDEX_UNITS_A,), (), 0, (), None),
    ("segment-unsorted", (_INDEX_UNITS_A,), (), 1, _INDEX_UNITS_B, None),
    ("fold", (_INDEX_UNITS_A, _INDEX_UNITS_F), (), 0, _INDEX_UNITS_B, None),
    ("fold-unchanged", (_INDEX_UNITS_A, _INDEX_UNITS_F), (), 0, _INDEX_UNITS_F, None),
    ("fold-unsorted", (_INDEX_UNITS_A, _INDEX_UNITS_F), (), 0, _INDEX_UNITS_B, None),
    ("fold-held", (_INDEX_UNITS_A, _INDEX_UNITS_F), (), 0, _INDEX_UNITS_B, unit_index.STORE_NAME),
    ("fold-own", (_INDEX_UNITS_A, _INDEX_UNITS_F), (), 0, _INDEX_UNITS_B, unit_index.INDEX_NAME),
    (
        "forced",
        (_INDEX_UNITS_A, *((_INDEX_UNITS_B, _INDEX_UNITS_C) * 10), _INDEX_UNITS_B),
        (),
        0,
        _INDEX_UNITS_C,
        unit_index.STORE_NAME,
    ),
    (
        "fold-many",
        (_INDEX_UNITS_A, *((_INDEX_UNITS_B, _INDEX_UNITS_C) * 8)),
        (),
        0,
        _INDEX_UNITS_B,
        None,
    ),
    ("unusable-head", (_INDEX_UNITS_A,), ("head-unparsed",), 0, _INDEX_UNITS_B, None),
    ("unusable-format", (_INDEX_UNITS_A,), ("head-format",), 0, _INDEX_UNITS_B, None),
    ("unusable-merge", (_INDEX_UNITS_A,), ("head-merge",), 0, _INDEX_UNITS_B, None),
    ("unusable-short", (_INDEX_UNITS_A,), ("base-short",), 0, _INDEX_UNITS_B, None),
    ("unusable-changed", (_INDEX_UNITS_A,), ("base-changed",), 0, _INDEX_UNITS_B, None),
    ("unusable-segment", (_INDEX_UNITS_A, _INDEX_UNITS_B), ("segment-header",), 0, _INDEX_UNITS_C, None),
    ("staging", (_INDEX_UNITS_A,), ("staging",), 0, _INDEX_UNITS_B, None),
    ("single", (), ("single",), 0, _INDEX_UNITS_B, None),
    ("single-same", (), ("single",), 0, _INDEX_UNITS_A, None),
    ("single-format", (), ("single-format",), 0, _INDEX_UNITS_B, None),
    ("single-not-gzip", (), ("single-not-gzip",), 0, _INDEX_UNITS_B, None),
    ("single-cut", (), ("single-cut",), 0, _INDEX_UNITS_B, None),
    ("manifest-unparsed", (_INDEX_UNITS_A,), ("manifest-unparsed",), 0, _INDEX_UNITS_B, None),
)


def _index_write_generated(_source: BuildSource) -> Iterator[Mapping[str, object]]:
    """The `index-write` stage's generated lines: for each case of `_INDEX_CASES`, a corpus holding the case's manifest and the index its prior passes wrote, each pass a pass of its own whose fold the unit store holds, with the case's edits made, and then `unit_index.write_index_lines` over the case's lines under the case's claim, under `generated:<name>`. Each line's input is the files the corpus holds before the write, its lines and the output whose claim the process holds; its value what the write did and its note (`_index_write_value`), or the class and text of what it raised, the files the corpus holds then (`_files_held`) and the counts lines it appended."""
    for name, passes, edits, manifest_at, units, holder in _INDEX_CASES:
        manifest = _INDEX_MANIFESTS[manifest_at]
        lines = _index_case_lines(units, manifest)
        if name.endswith("unsorted"):
            lines.reverse()
        with tempfile.TemporaryDirectory(prefix="index-write-") as scratch, _counts_named(None):
            corpus, cycle_dir, counts = (
                Path(scratch) / "out",
                Path(scratch) / "cycle",
                Path(scratch) / "counts",
            )
            corpus.mkdir()
            (corpus / "manifest.json").write_bytes(_manifest_bytes(_INDEX_MANIFESTS[0]))
            for prior in passes:
                with _claim_state(cycle_dir, None, unit_index.STORE_NAME):
                    unit_index.write_index_lines(corpus, _index_case_lines(prior, _INDEX_MANIFESTS[0]))
            if manifest_at:
                (corpus / "manifest.json").write_bytes(_manifest_bytes(manifest))
            for edit in edits:
                _index_case_edits(corpus, edit)
            files = {path.name: path.read_bytes() for path in sorted(corpus.iterdir())}
            counts.write_bytes(b"")
            outcome: object
            with _counts_named(counts), _claim_state(cycle_dir, None, holder):
                try:
                    written = unit_index.write_index_lines(corpus, lines)
                    outcome = (*_index_write_value(written), written.note())
                except (OSError, EOFError, ValueError) as error:
                    outcome = {"raised": type(error).__name__, "message": str(error)}
            yield {
                "key": f"generated:{name}",
                "input": (files, lines, holder),
                "value": (outcome, _files_held(corpus), counts.read_bytes()),
            }


# The build's stage registry, in the order `compare` runs the upstream stages' compares before a boundary stage's (the plan's § Stage fixtures). The task that ports a stage adds the stage here. `build-input` records no hooked call: `build_dump` records it from the values it writes `BUILD_INPUT_FILE` from.
BUILD_STAGES: dict[str, Stage[BuildSource]] = {
    BUILD_INPUT_STAGE: Stage(BUILD_INPUT_STAGE, {}, run="build", kind="boundary"),
    SINK_STAGE: Stage(
        SINK_STAGE,
        {hook: _sink_call(hook) for hook in _SINK_SIGNATURES},
        _sink_generated,
        outputs=("returned", "counts", "after"),
        run="build",
    ),
    CONTAINERS_STAGE: Stage(
        CONTAINERS_STAGE,
        {
            "gzip.GzipFile.__init__": _gzip_opened,
            "gzip.GzipFile.write": _container_write,
            "gzip.GzipFile.close": _gzip_closing,
            "gzip.compress": _gzip_compressed,
            "unit_index._Members.__init__": _members_opened,
            "unit_index._Members.write": _container_write,
            "unit_index._Members.__exit__": _members_exiting,
            "unit_index.deflate_fragment": _fragment_deflated,
            "unit_index.inflate_fragment": _fragment_inflating,
        },
        _containers_generated,
        outputs=("output",),
        run="build",
    ),
    JSON_FORMS_STAGE: Stage(
        JSON_FORMS_STAGE,
        {"json.dumps": _json_dumped, "json.loads": _json_loaded},
        _json_forms_generated,
        run="build",
    ),
    SAMPLE_STAGE: Stage(
        SAMPLE_STAGE, {"build._verification_sample": _verification_sampled}, _sample_generated, run="build"
    ),
    ROW_DELTA_READ_STAGE: Stage(
        ROW_DELTA_READ_STAGE,
        {
            "row_delta.read_head": _head_read,
            "row_delta.open_parts": _parts_opened,
            "row_delta.check_segment_header": _segment_header_checked,
        },
        _row_delta_generated,
        run="build",
    ),
    ROW_DELTA_WRITE_STAGE: Stage(
        ROW_DELTA_WRITE_STAGE,
        {
            "row_delta.places_between": _places_cut,
            "row_delta.row_line": _row_lined,
            "row_delta.row_length": _row_measured,
            "row_delta.segment_header": _segment_headed,
            "row_delta.write_part": _part_written,
            "row_delta.write_segment": _segment_written,
            "row_delta.StreamedPart.close": _streamed_closed,
            "row_delta.commit": _head_committed,
            "row_delta.remove_unlisted": _unlisted_removed,
            "row_delta.remove_staging": _staging_removed,
            "row_delta.writes_base": _share_decided,
            "row_delta.fold_due": _fold_decided("fold-due", "fold_due", _FOLD_DUE),
            "row_delta.fold_forced": _fold_decided("fold-forced", "fold_forced", _FOLD_FORCED),
            "row_delta.claim_fold": _fold_claimed,
            "row_delta.with_segment": _segment_added,
        },
        _row_delta_write_generated,
        run="build",
    ),
    AUDIT_LAYOUT_STAGE: Stage(
        AUDIT_LAYOUT_STAGE,
        {"build.audit_layout": _layout_found},
        _audit_layout_generated,
        run="build",
        kind="boundary",
    ),
    STREAM_STAGE: Stage(
        STREAM_STAGE,
        {"build.stream_workload": _stream_walked, "audit_stream.window_ranges": _ranges_split},
        _stream_generated,
        run="build",
        kind="boundary",
    ),
    SIGNATURE_STORE_STAGE: Stage(
        SIGNATURE_STORE_STAGE,
        {
            "unit_cache.SignatureArrays.__init__": _signatures_made,
            "unit_cache.SignatureArrays.load": _signatures_loaded,
            **{f"unit_cache.SignatureArrays.{name}": _signature_followed(name) for name in SIGNATURE_CALLS},
            "unit_cache.write_signature_arrays": _signatures_writing,
            "unit_cache.signature_miss_note": _signature_noted,
        },
        _signature_store_generated,
        run="build",
    ),
    SIGNATURE_STAGE: Stage(
        SIGNATURE_STAGE,
        {"build._shape_misses": _misses_shaped, "build._LateShaper.__call__": _late_shaped},
        _signature_generated,
        outputs=("signature", "value"),
        run="build",
    ),
    KEY_ONLY_STAGE: Stage(
        KEY_ONLY_STAGE,
        {
            "build._find_misses": _misses_found,
            "build._shape_found_misses": _found_misses_shaped,
            "build._LateShaper.release": _late_released,
        },
        _key_only_generated,
        run="build",
        kind="boundary",
    ),
    COLUMNS_STAGE: Stage(
        COLUMNS_STAGE,
        {
            **{f"columns.{owner}.__init__": _columns_made(owner) for owner in COLUMNS_FOLLOWED},
            **{
                f"columns.{owner}.{name}": _columns_called(name)
                for owner in COLUMNS_FOLLOWED
                for name in COLUMNS_CALLS[owner]
                if name != "__init__"
            },
            **{f"columns.WindowIndex.{name}": _window_called(name) for name in COLUMNS_CALLS["WindowIndex"]},
            "columns.window_word": _window_worded,
            "audit.window_word": _window_worded,
            "unit_cache.sorted_by_word": _words_sorted,
            "unit_store.sorted_by_word": _words_sorted,
        },
        _columns_generated,
        run="build",
    ),
    PERMUTATION_STAGE: Stage(
        PERMUTATION_STAGE,
        {
            "audit._counting_permutation": _counted,
            "audit._triage_permutation": _triaged,
            "audit.family_ranks": _family_ranked,
            "build.family_ranks": _family_ranked,
            "build.triage_key": _triage_keyed,
        },
        _permutation_generated,
        run="build",
    ),
    MERGE_STAGE: Stage(
        MERGE_STAGE,
        {"audit._merge_members": _members_merged},
        _merge_generated,
        run="build",
    ),
    WORKLOAD_STAGE: Stage(
        WORKLOAD_STAGE, {"build.stream_workload": _workload_streamed}, run="build", kind="boundary"
    ),
    PREMERGE_STAGE: Stage(
        PREMERGE_STAGE, {"facts.PremergeSnapshot.rebase": _premerge_rebased}, run="build", kind="boundary"
    ),
    LOAD_STAGE: Stage(LOAD_STAGE, {"build._load_corpus": _corpus_loaded}, run="build", kind="boundary"),
    STORE_RECORDS_STAGE: Stage(
        STORE_RECORDS_STAGE,
        {
            "unit_cache._parsed_cached_unit": _records_parsed,
            "unit_cache.record_line": _records_lined,
            "unit_cache.prior_record_or_none": _records_sliced,
            "unit_cache.named_ordinal": _records_named,
            "unit_cache.id_word": _records_called("id-word"),
            "unit_cache.primary_units_crc": _records_called("primary-units-crc"),
            "unit_cache.is_content_id": _records_called("content-id"),
        },
        _store_records_generated,
        run="build",
    ),
    STORE_LOAD_STAGE: Stage(
        STORE_LOAD_STAGE,
        {f"unit_store.UnitStore.{method}": _unit_store_followed(method) for method in STORE_FOLLOWED},
        _store_load_generated,
        run="build",
    ),
    SCAN_STAGE: Stage(
        SCAN_STAGE,
        {
            "unit_cache.open_store": _store_opened,
            "unit_cache.scan_store": _store_scanned,
            "unit_cache.scan_marked": _marked_scanned,
            "unit_cache.locate_prior_fragments": _fragments_located,
            "unit_cache.LocatedFragments.find": _fragment_found,
            "unit_cache.store_miss_note": _scan_called(SCAN_MISS_NOTE_KEY, lambda note: note),
            "unit_cache.rederiving_moved": _scan_called(SCAN_REDERIVING_KEY, lambda moved: moved),
            "unit_cache.adopt_single_file": _scan_called(SCAN_ADOPT_KEY, lambda head: head.document()),
        },
        _scan_generated,
        run="build",
        kind="boundary",
    ),
    PLAN_STAGE: Stage(PLAN_STAGE, {"build._phase_timing": _plan_timed}, run="build", kind="boundary"),
    UNITS_STAGE: Stage(
        UNITS_STAGE, {"build._RecomputeRunner.phase1": _units_drafted}, run="build", kind="boundary"
    ),
    TRIAGE_STAGE: Stage(
        TRIAGE_STAGE, {"build.assign_batches": _batches_assigned}, run="build", kind="boundary"
    ),
    PASSES_STAGE: Stage(
        PASSES_STAGE,
        {"build._phase_timing": _passes_timed},
        _passes_generated,
        run="build",
        kind="boundary",
    ),
    ROUTE_STAGE: Stage(
        ROUTE_STAGE, {"build._write_runs": _runs_cut}, _route_generated, run="build", kind="boundary"
    ),
    SHARDS_STAGE: Stage(
        SHARDS_STAGE,
        {
            "build._ShardWriter.open": _shard_opened,
            "build._ShardWriter.holds": _shard_holds,
            "build._ShardWriter.keep": _shard_keep,
            "build._ShardWriter.add_bytes": _shard_added,
            "build._ShardWriter.close": _shard_closed,
        },
        _shards_generated,
        run="build",
    ),
    PRIOR_READ_STAGE: Stage(
        PRIOR_READ_STAGE,
        {
            **{f"unit_cache.PriorFragmentReader.{name}": _prior_reading(name) for name in PRIOR_READ_FORMS},
            "build._FragmentArena.read": _arena_read,
        },
        _prior_read_generated,
        run="build",
    ),
    EMIT_STAGE: Stage(EMIT_STAGE, {"build._emission": _emitted}, run="build"),
    CHECK_PATCHED_STAGE: Stage(
        CHECK_PATCHED_STAGE,
        {"build.check_unit": _patched_checked},
        _check_patched_generated,
        run="build",
    ),
    CORPUS_CHECK_STAGE: Stage(
        CORPUS_CHECK_STAGE,
        {
            "build._CorpusCheck.class_start": _check_class_start,
            "build._CorpusCheck.unit": _check_fed,
            "build._CorpusCheck.class_end": _check_class_end,
            "build._CorpusCheck.take_facts": _check_take_facts,
            "build._CorpusCheck.absorb": _check_absorb,
            "build._CorpusCheck.finish": _check_finished,
        },
        _corpus_check_generated,
        run="build",
    ),
    SIDECAR_LINES_STAGE: Stage(
        SIDECAR_LINES_STAGE,
        {
            "build._SidecarLines.__init__": _sidecars_opened,
            "build._SidecarLines.unit": _sidecar_unit,
            "build._SidecarLines.cached": _sidecar_cached,
            "build._SidecarLines.close": _sidecars_closed,
            "gzip.GzipFile.write": _sidecar_gzip_write,
            "app_index.LocatorBlocks.add": _sidecar_locator_add,
            "unit_index.LineCursor.take": _sidecar_take,
        },
        _sidecar_lines_generated,
        run="build",
    ),
    WRITE_STAGE: Stage(
        WRITE_STAGE, {"build._write_json": _manifest_writing}, _write_generated, run="build", kind="boundary"
    ),
    MANIFEST_STAGE: Stage(
        MANIFEST_STAGE,
        {"build._prune_orphan_shards": _manifest_pruning},
        _manifest_generated,
        run="build",
        kind="boundary",
    ),
    INDEX_WRITE_STAGE: Stage(
        INDEX_WRITE_STAGE,
        {"unit_index.write_index_lines": _index_writing},
        _index_write_generated,
        run="build",
        kind="boundary",
    ),
}


def default_every(source: str, every: int | None) -> int:
    """The stride of a dump's boundary stages: every unit on the mini, which refuses another stride, and `every` or `LIVE_EVERY` on live."""
    if source == "mini":
        if every not in (None, 1):
            raise SystemExit("a mini dump records every unit; --every strides a live dump")
        return 1
    if every is not None and every < 1:
        raise SystemExit(f"--every {every} is not a stride; it takes a positive count of units")
    return LIVE_EVERY if every is None else every


def single_build(scenario: BuildScenario) -> BuildCase:
    """The scenario's one build; a scenario of several builds is refused, since a dump records one build's calls and boundaries."""
    if len(scenario.cases) != 1:
        raise SystemExit(
            f"the {scenario.name} scenario runs {len(scenario.cases)} builds; "
            "a build dump or selfcheck takes a scenario of one"
        )
    return scenario.cases[0]


def build_widths(case: BuildCase) -> tuple[int, int]:
    """The units width a dump's build runs at, the case's own or the harness's default (`default_width`), and its signature shaping's (`signature_width`)."""
    jobs = default_width() if case.jobs is None else case.jobs
    return jobs, signature_width(case.jobs)


def open_build_dump(source: str, scenario: str, out: Path, every: int | None) -> BuildSource:
    """Prepare the named scenario (`open_build_source`) for a dump into `out` at the stride `default_every` gives."""
    stride = default_every(source, every)
    prepared = open_build_source(source, scenario)
    case = single_build(prepared)
    jobs, signature_jobs = build_widths(case)
    return BuildSource(prepared, case, out, stride, jobs, signature_jobs)


def clone_build_inputs(inputs: BuildInputs, target: Path) -> dict[str, str]:
    """Clone what a build of `inputs` reads, but the spec, into `target`, modification times kept (`_clone_file`): the audit's files (`_audit_names`), the ledger, the after font and the site's before and Junior fonts, the subset tables and their pack. Return each input's clone by its role, as a path relative to `target`'s parent."""
    roles = {
        "ledger": inputs.ledger,
        "after_font": inputs.after_font,
        "before_font": build.SITE_BEFORE_FONT,
        "junior_font": build.SITE_JUNIOR_FONT,
        "subset_pack": inputs.subset_pack,
        **{
            f"subset_{config}": inputs.subset_dir / f"baseline-{config}.subset.tsv.gz"
            for config in ACCEPTANCE_CONFIGS
        },
    }
    files = {name: inputs.audit.parent / name for name in _audit_names(inputs.audit)}
    for role, path in roles.items():
        if files.setdefault(path.name, path) != path:
            raise SystemExit(f"the build's inputs {files[path.name]} and {path} share one name")
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for name, path in files.items():
        _clone_file(path, target / name)
    base = target.name
    cloned = {role: f"{base}/{path.name}" for role, path in roles.items()}
    return {"audit": f"{base}/{inputs.audit.name}", "subset_dir": base, **cloned}


def write_dump_init(inputs: BuildInputs, dump: Path, cloned: Mapping[str, str]) -> Path:
    """Write the units init a build of `inputs` hands its units engine, as `_RecomputeRunner.__init__` builds it, over the dump's clones of the fonts, the subset tables and their pack, into `dump/DUMP_INIT/` beside its spec dump and font facts (`units_exec.write_init`), and return its path."""
    identical_classes = frozenset(entry.id for entry in load_ledger(inputs.ledger) if entry.ink_identical)
    init = _worker_init(
        dump / cloned["after_font"],
        dump / cloned["subset_dir"],
        dump / cloned["subset_pack"],
        inputs.spec_root,
        identical_classes,
        before_font=dump / cloned["before_font"],
        junior_font=dump / cloned["junior_font"],
    )
    directory = _cleared(dump / DUMP_INIT)
    return units_exec.write_init(init, directory)


@contextlib.contextmanager
def _as_build_child(scratch: Path) -> Iterator[None]:
    """Hold this process as `run_python_build` holds its child for the length of one build: the child's environment (`_build_environment`) with its counts file under `scratch`, and the journal and the cycle directory redirected there, so the build's fold claims start from none, as a fresh process's do (`row_delta.claim_fold`). Everything is put back on exit."""
    scratch.mkdir(parents=True, exist_ok=True)
    counts = scratch / "counts"
    counts.write_bytes(b"")
    cycle_var = scratch / "cycle"
    cycle_var.mkdir(exist_ok=True)
    held = dict(os.environ)
    environment = _build_environment(counts, None)
    journal, cycle = cycle_timings.JOURNAL, cycle_paths.CYCLE_VAR
    os.environ.clear()
    os.environ.update(environment)
    cycle_timings.JOURNAL = scratch / "journal.ndjson"
    cycle_paths.CYCLE_VAR = cycle_var
    try:
        yield
    finally:
        cycle_timings.JOURNAL, cycle_paths.CYCLE_VAR = journal, cycle
        os.environ.clear()
        os.environ.update(held)


def _build_here(source: BuildSource, out: Path) -> None:
    inputs = source.case.inputs
    build.build_m1(
        out,
        audit_path=inputs.audit,
        ledger_path=inputs.ledger,
        subset_dir=inputs.subset_dir,
        after_font=inputs.after_font,
        jobs=source.jobs,
        signature_jobs=source.signature_jobs,
        recompute_all_units=inputs.recompute_all,
        spec_root=inputs.spec_root,
        subset_pack=inputs.subset_pack,
        write_jobs=1,
    )


def run_build_here(source: BuildSource, out: Path, scratch: Path) -> None:
    """Run the source's build into `out` in this process (`_as_build_child`), with `python_build_child`'s arguments at the source's widths and one write job, so every write runs here."""
    with _as_build_child(scratch):
        _build_here(source, out)


def build_input_values(
    source: BuildSource, out: Path, scratch: Path, init: Path
) -> tuple[dict, tuple[int, ...]]:
    """The values of the Rust build's input (`build._rust_inputs`) for the build `_build_here` runs into `out`, with `build_m1`'s defaults for every argument it leaves out, over the scenario's own audit held as the build holds it (`audit.AuditParts`), the units init `init` and the spill directory `scratch`; and the size of each audit part held, base first. Run it in the build's environment (`_as_build_child`), whose cycle and counts file the input names."""
    inputs = source.case.inputs
    parts = audit.AuditParts(inputs.audit)
    try:
        values = build._rust_inputs(
            parts,
            out_dir=out,
            audit_path=inputs.audit,
            ledger_path=inputs.ledger,
            subset_dir=inputs.subset_dir,
            before_font=build.SITE_BEFORE_FONT,
            after_font=inputs.after_font,
            junior_font=build.SITE_JUNIOR_FONT,
            repo_root=REPO_ROOT,
            batch_size=BATCH_SIZE,
            static_dir=build.STATIC_DIR,
            jobs=source.jobs,
            signature_jobs=source.signature_jobs,
            recompute_all_units=inputs.recompute_all,
            spec_root=inputs.spec_root,
            subset_digests=table_digests(inputs.subset_dir, ACCEPTANCE_CONFIGS),
            gates_idle_jobs=None,
            gates_idle_marker=None,
            write_jobs=1,
            units_init=init,
            scratch=scratch,
        )
        held = tuple(os.fstat(descriptor).st_size for descriptor in parts.descriptors)
    finally:
        parts.close()
    return values, held


def build_input_boundary(values: Mapping[str, object], held: Sequence[int]) -> Boundary:
    """The `build-input` stage's lines: each input value under `input:<field>`, in `corpus_exec.INPUT_FIELDS` order, and the held audit parts' sizes under `audit.held`."""
    lines = [{"key": f"input:{name}", "value": value} for name, value in values.items()]
    return Boundary(0, {}, [*lines, {"key": "audit.held", "value": tuple(held)}])


def _build_prior(source: BuildSource) -> Path:
    prior = source.case.prior
    assert prior is not None, "a scenario's first build names its prior"
    return prior


def build_dump(source: BuildSource, stages: Sequence[Stage[BuildSource]]) -> None:
    """Write a build dump under `source.out`: the prior corpus as the build finds it (`DUMP_PRIOR`), clones of what it reads (`DUMP_INPUTS`, `clone_build_inputs`), the units init (`write_dump_init`), `source.json`, and the Rust build's input for the run's values without descriptors (`BUILD_INPUT_FILE`, `build_input_values`), taken in the run's environment; then, when any stage is selected, record the `build-input` stage's lines from those values when it is one of them, run the build in this process over a clone of the prior, with every build hook point wrapped and the selected stages recording, and add each stage's generated lines. The build reads the scenario's own inputs, whose paths its manifest names, and the build's tree is deleted once it returns."""
    out = source.out
    out.mkdir(parents=True, exist_ok=True)
    prior = out / DUMP_PRIOR
    _clone_tree(_build_prior(source), prior)
    cloned = clone_build_inputs(source.case.inputs, out / DUMP_INPUTS)
    init = write_dump_init(source.case.inputs, out, cloned)
    (out / SOURCE_FILE).write_text(
        json.dumps(source.describe(cloned, init), indent=1) + "\n", encoding="ascii"
    )
    sink = _Sink(out, stages, source.every) if stages else None
    if any(stage.name == SINK_STAGE for stage in stages):
        shutil.rmtree(out / SINK_BLOBS, ignore_errors=True)
    if any(stage.name == CONTAINERS_STAGE for stage in stages):
        shutil.rmtree(out / CONTAINER_BLOBS, ignore_errors=True)
    if any(stage.name == ROW_DELTA_READ_STAGE for stage in stages):
        shutil.rmtree(out / ROW_DELTA_BLOBS, ignore_errors=True)
    if any(stage.name == ROW_DELTA_WRITE_STAGE for stage in stages):
        shutil.rmtree(out / ROW_DELTA_WRITE_BLOBS, ignore_errors=True)
    if any(stage.name == AUDIT_LAYOUT_STAGE for stage in stages):
        shutil.rmtree(out / AUDIT_LAYOUT_BLOBS, ignore_errors=True)
    if any(stage.name == STREAM_STAGE for stage in stages):
        shutil.rmtree(out / STREAM_BLOBS, ignore_errors=True)
    if any(stage.name == SIGNATURE_STORE_STAGE for stage in stages):
        shutil.rmtree(out / SIGNATURE_STORE_BLOBS, ignore_errors=True)
    if any(stage.name == KEY_ONLY_STAGE for stage in stages):
        shutil.rmtree(out / KEY_ONLY_BLOBS, ignore_errors=True)
    if any(stage.name == SCAN_STAGE for stage in stages):
        shutil.rmtree(out / SCAN_BLOBS, ignore_errors=True)
    if any(stage.name == SHARDS_STAGE for stage in stages):
        shutil.rmtree(out / SHARDS_BLOBS, ignore_errors=True)
    if any(stage.name == PRIOR_READ_STAGE for stage in stages):
        shutil.rmtree(out / PRIOR_READ_BLOBS, ignore_errors=True)
    if any(stage.name == SIDECAR_LINES_STAGE for stage in stages):
        shutil.rmtree(out / SIDECAR_LINES_BLOBS, ignore_errors=True)
    scratch = out / DUMP_SCRATCH
    shutil.rmtree(scratch, ignore_errors=True)
    try:
        built = scratch / "out"
        with _as_build_child(scratch / "run"):
            values, held = build_input_values(source, built, scratch / "spill", init)
            corpus_exec.write_input(values, out / BUILD_INPUT_FILE)
            if sink is not None:
                if any(stage.name == BUILD_INPUT_STAGE for stage in stages):
                    sink.record(BUILD_INPUT_STAGE, build_input_boundary(values, held))
                _clone_tree(prior, built)
                with (
                    wrapped(stages, sink, BUILD_HOOKS),
                    _sink_blobs_at(out / SINK_BLOBS),
                    _container_blobs_at(out / CONTAINER_BLOBS),
                    _row_delta_blobs_at(out / ROW_DELTA_BLOBS),
                    _row_delta_write_blobs_at(out / ROW_DELTA_WRITE_BLOBS),
                    _signature_store_blobs_at(out / SIGNATURE_STORE_BLOBS),
                    _shards_blobs_at(out / SHARDS_BLOBS),
                    _prior_read_blobs_at(out / PRIOR_READ_BLOBS),
                    _sidecar_blobs_at(out / SIDECAR_LINES_BLOBS),
                    _columns_following(),
                    _unit_store_following(),
                ):
                    _build_here(source, built)
        shutil.rmtree(scratch)
        if sink is not None:
            for stage in stages:
                if stage.generated is not None:
                    for fields in stage.generated(source):
                        sink.write(stage.name, "generated", fields)
    finally:
        if sink is not None:
            sink.close()
    print(
        f"{out}: {source.scenario.source}-{source.scenario.name} {source.case.name}, "
        f"{SOURCE_FILE}, {BUILD_INPUT_FILE}, {DUMP_PRIOR}/, {DUMP_INPUTS}/ and {init.relative_to(out)}",
        file=sys.stderr,
    )
    for stage in stages:
        print(out / f"{stage.name}.ndjson", file=sys.stderr)


def _tree_files(root: Path) -> dict[str, Path]:
    return {path.relative_to(root).as_posix(): path for path in root.rglob("*") if not path.is_dir()}


def tree_differences(left: Path, right: Path) -> tuple[int, list[str]]:
    """How many files two output trees hold between them, and each relative path that only one holds or whose bytes differ, sorted."""
    left_files, right_files = _tree_files(left), _tree_files(right)
    names = sorted(left_files.keys() | right_files.keys())
    differing = [
        name
        for name in names
        if name not in left_files
        or name not in right_files
        or not filecmp.cmp(left_files[name], right_files[name], shallow=False)
    ]
    return len(names), differing


def build_selfcheck(source: str, scenario: str) -> bool:
    """Prepare the scenario and run its one build twice in this process, each over its own clone of the prior (`run_build_here`): once as the build runs and once with every build hook point wrapped and every build stage recording into a temporary directory. Print what differs and return whether the two output trees are identical."""
    prepared = open_build_source(source, scenario)
    case = single_build(prepared)
    jobs, signature_jobs = build_widths(case)
    with tempfile.TemporaryDirectory(prefix="build-stage-selfcheck-", dir=prepared.directory) as held:
        scratch = Path(held)
        built = BuildSource(
            prepared, case, scratch / "stages", default_every(source, None), jobs, signature_jobs
        )
        plain, recorded = scratch / "plain", scratch / "wrapped"
        _clone_tree(_build_prior(built), plain)
        run_build_here(built, plain, scratch / "plain-run")
        built.out.mkdir()
        stages = list(BUILD_STAGES.values())
        sink = _Sink(built.out, stages, built.every)
        try:
            _clone_tree(_build_prior(built), recorded)
            with (
                wrapped(stages, sink, BUILD_HOOKS),
                _sink_blobs_at(built.out / SINK_BLOBS),
                _container_blobs_at(built.out / CONTAINER_BLOBS),
                _row_delta_blobs_at(built.out / ROW_DELTA_BLOBS),
                _row_delta_write_blobs_at(built.out / ROW_DELTA_WRITE_BLOBS),
                _signature_store_blobs_at(built.out / SIGNATURE_STORE_BLOBS),
                _shards_blobs_at(built.out / SHARDS_BLOBS),
                _prior_read_blobs_at(built.out / PRIOR_READ_BLOBS),
                _sidecar_blobs_at(built.out / SIDECAR_LINES_BLOBS),
                _columns_following(),
                _unit_store_following(),
            ):
                run_build_here(built, recorded, scratch / "wrapped-run")
        finally:
            sink.close()
        files, differing = tree_differences(plain, recorded)
    print(
        f"{source}-{scenario} {case.name}: the plain and wrapped builds ({len(stages)} stages, "
        f"{len(BUILD_HOOKS)} hook points) wrote {files:,} files, {len(differing):,} differing",
        file=sys.stderr,
    )
    for name in differing[:EXAMPLES_SHOWN]:
        print(f"  differs: {name}", file=sys.stderr)
    if not differing:
        print("selfcheck: the wrapped build writes the plain build's tree byte for byte", file=sys.stderr)
    return not differing


# --- commands -----------------------------------------------------------------------------------


def _selected[S](names: Sequence[str] | None, registry: Mapping[str, Stage[S]]) -> list[Stage[S]]:
    if not names:
        return list(registry.values())
    unknown = [name for name in names if name not in registry]
    if unknown:
        known = ", ".join(registry) or "none yet"
        raise SystemExit(f"unknown stage {', '.join(unknown)}; the registered stages are: {known}")
    return [registry[name] for name in dict.fromkeys(names)]


def dump(source: Source, stages: Sequence[Stage[Source]], out: Path) -> None:
    """Write the units file and every selected stage's lines under `out`: the worker's run records the stages with recorders, and each stage's generator adds its generated lines after them."""
    out.mkdir(parents=True, exist_ok=True)
    write_units_file(source, out)
    print(
        f"{out / UNITS_FILE}: {source.unit_count():,} units in {len(source.handouts)} handouts",
        file=sys.stderr,
    )
    if not stages:
        return
    sink = _Sink(out, stages)
    try:
        if any(stage.recorders for stage in stages):
            with wrapped(stages, sink):
                run = run_worker(source)
            print(f"recorded {run.units:,} units", file=sys.stderr)
        for stage in stages:
            if stage.generated is not None:
                for fields in stage.generated(source):
                    sink.write(stage.name, "generated", fields)
    finally:
        sink.close()
    for stage in stages:
        print(out / f"{stage.name}.ndjson", file=sys.stderr)


def selfcheck(source: Source) -> bool:
    """Run the worker plain and wrapped (the module docstring), print both digests and the round trip's result, and return whether everything agreed."""
    plain = run_worker(source)
    with tempfile.TemporaryDirectory(prefix="units-stage-selfcheck-") as scratch:
        sink = _Sink(Path(scratch), list(STAGES.values()))
        try:
            with wrapped(list(STAGES.values()), sink):
                recorded = run_worker(source)
        finally:
            sink.close()
    print(
        f"plain: {plain.units:,} units, {plain.contract_errors} contract errors, digest {plain.digest}",
        file=sys.stderr,
    )
    print(
        f"wrapped ({len(STAGES)} stages, {len(HOOKS)} hook points): {recorded.units:,} units, "
        f"{recorded.contract_errors} contract errors, digest {recorded.digest}",
        file=sys.stderr,
    )
    ok = True
    if plain.units != source.unit_count():
        print(
            f"the worker answered {plain.units:,} of {source.unit_count():,} units handed out",
            file=sys.stderr,
        )
        ok = False
    if plain.digest != recorded.digest or plain.units != recorded.units:
        print("the wrapped run's projections differ from the plain run's", file=sys.stderr)
        ok = False
    for label, run in (("plain", plain), ("wrapped", recorded)):
        if run.unfaithful:
            print(
                f"{label}: {len(run.unfaithful):,} projections did not survive the typed encoding, "
                f"ordinals {run.unfaithful[:10]}",
                file=sys.stderr,
            )
            ok = False
    if ok:
        print(
            "selfcheck: the wrapped run matches the plain run and every projection round-trips",
            file=sys.stderr,
        )
    return ok


def source_init(description: Mapping[str, object]) -> dict:
    """The worker `init` of the source a dump's `source.json` names, built as `open_source` builds it but, for a live source, without loading the corpus, which the init does not read."""
    name = description.get("source")
    if name == "mini":
        return _mini_source().init
    if name == "live":
        identical_classes = frozenset(
            entry.id for entry in load_ledger(build.M1_LEDGER) if entry.ink_identical
        )
        return _worker_init(
            build.M1_AFTER_FONT, build.M1_SUBSETS, build.M1_SUBSETS / PACK_NAME, REPO_ROOT, identical_classes
        )
    raise SystemExit(f"the dump names source {name!r}; the sources are {', '.join(SOURCES)}")


# How many disagreements `compare` prints.
EXAMPLES_SHOWN = 5


def _answers(lines: Sequence[str], fields: Sequence[str], origin: str) -> dict[str, dict[str, object]]:
    """Each line's `fields` decoded from the typed encoding, by the line's key; a key on two lines is refused."""
    answers: dict[str, dict[str, object]] = {}
    for line in lines:
        record = json.loads(line)
        key = record.get("key")
        if not isinstance(key, str):
            raise SystemExit(f"{origin}: a line has no key: {line[:200]}")
        if key in answers:
            raise SystemExit(f"{origin}: the key {key!r} is on two lines")
        answers[key] = {name: decode(record[name]) for name in fields if name in record}
    return answers


def _difference(expected: object, actual: object) -> str:
    left, right = compact(encode(expected)), compact(encode(actual))
    offset = next(
        (index for index, (x, y) in enumerate(zip(left, right)) if x != y), min(len(left), len(right))
    )
    start = max(0, offset - 80)
    return f"at character {offset}:\n    python: …{left[start:offset + 80]}…\n    rust:   …{right[start:offset + 80]}…"


def compared_fields(stage: Stage[Any], names: str | None) -> tuple[str, ...]:
    """The outputs `compare` checks: every output of the stage, or the comma-separated output fields and field groups `names` lists, in that order, each once. A name the stage has neither as an output nor as a group is refused."""
    if names is None:
        return stage.outputs
    chosen: list[str] = []
    for name in names.split(","):
        if name in stage.field_groups:
            chosen += stage.field_groups[name]
        elif name in stage.outputs:
            chosen.append(name)
        else:
            known = ", ".join([*stage.field_groups, *stage.outputs])
            raise SystemExit(f"the {stage.name} stage has no field or field group {name!r}; it has {known}")
    return tuple(dict.fromkeys(chosen))


def _run_worker_stage(stage: Stage[Any], stages: Path, stage_file: Path) -> tuple[dict, int, bytes, bytes]:
    """Run a worker stage's Rust side over a dump with an init written for the dump's source (`source_init`, `units_exec.write_init`) into a directory held for the run, and return that init with the process's status and output."""
    init = source_init(json.loads((stages / SOURCE_FILE).read_text(encoding="ascii")))
    with tempfile.TemporaryDirectory(prefix="units-stage-compare-") as scratch:
        init_path = units_exec.write_init(init, Path(scratch))
        process = units_exec.spawn(["stage", stage.name, f"--init={init_path}", str(stage_file)])
        stdout, stderr = process.communicate()
    return init, process.returncode, stdout, stderr


def _run_build_stage(
    stage: Stage[Any], stages: Path, stage_file: Path, arguments: Sequence[str] = ()
) -> tuple[dict, int, bytes, bytes]:
    """Run a build stage's Rust side over a dump: open the dump's audit clone as the build holds an audit (`audit.AuditParts`), write the dump's `BUILD_INPUT_FILE` again with those descriptors into a directory held for the run, and run the stage with them inherited, `arguments` after its `--init`. Return the input's document with the process's status and output."""
    description = json.loads((stages / SOURCE_FILE).read_text(encoding="ascii"))
    document = json.loads((stages / BUILD_INPUT_FILE).read_text(encoding="ascii"))
    parts = audit.AuditParts(stages / description["inputs"]["audit"])
    try:
        document["audit"]["descriptors"] = list(parts.descriptors)
        with tempfile.TemporaryDirectory(prefix="build-stage-compare-") as scratch:
            input_path = Path(scratch) / BUILD_INPUT_FILE
            input_path.write_text(compact(document) + "\n", encoding="ascii")
            process = units_exec.spawn(
                ["stage", stage.name, f"--init={input_path}", *arguments, str(stage_file)],
                pass_fds=parts.descriptors,
            )
            stdout, stderr = process.communicate()
    finally:
        parts.close()
    return document, process.returncode, stdout, stderr


def build_compare(
    stage: Stage[BuildSource],
    stages: Path,
    checked: Sequence[str] | None = None,
    write_threads: int | None = None,
) -> bool:
    """`compare` a build stage. Before a boundary stage's own compare, compare each build stage registered ahead of it (`BUILD_STAGES`) whose lines the dump holds, every output checked, and stop at the first that differs, which names the stage a disagreement starts in. `write_threads`, which only the `write` stage takes, sets the width its own Rust side writes at in place of the dump's."""
    if write_threads is not None and stage.name != WRITE_STAGE:
        raise SystemExit(
            f"--write-threads sets the {WRITE_STAGE} stage's width, not the {stage.name} stage's"
        )
    if stage.kind == "boundary":
        for upstream in BUILD_STAGES.values():
            if upstream.name == stage.name:
                break
            if (stages / f"{upstream.name}.ndjson").is_file() and not compare(upstream, stages):
                print(
                    f"compare {stage.name}: the upstream {upstream.name} stage differs first", file=sys.stderr
                )
                return False
    return compare(
        stage, stages, checked, () if write_threads is None else (f"--write-threads={write_threads}",)
    )


def compare(
    stage: Stage[Any], stages: Path, checked: Sequence[str] | None = None, arguments: Sequence[str] = ()
) -> bool:
    """Run the Rust side of `stage` over the dump under `stages` and report every line whose expected output it does not reproduce, a boundary stage's `column:` line by its count and digest (`COLUMN_FIELDS`), every problem the stage's answer checks find in an answer, every key it does not answer, and every key it answers that the dump does not hold. Only the outputs `checked` names are checked, every output when None. The crate is built first (`units_exec.ensure_built`); a worker stage reads an init written for the dump's source (`_run_worker_stage`), and a build stage the dump's build input with the dump's audit clone held and inherited (`_run_build_stage`), with `arguments` after its `--init`, whose document its answer checks see in place of the init."""
    outputs = tuple(checked) if checked is not None else stage.outputs
    stage_file = stages / f"{stage.name}.ndjson"
    if not stage_file.is_file():
        raise SystemExit(f"{stage_file} is missing; dump --stage {stage.name} writes it")
    expected = _answers(
        stage_file.read_text(encoding="ascii").splitlines(), (*outputs, *COLUMN_FIELDS), str(stage_file)
    )
    units_exec.ensure_built()
    if stage.run == "build":
        init, returncode, stdout, stderr = _run_build_stage(stage, stages, stage_file, arguments)
    else:
        init, returncode, stdout, stderr = _run_worker_stage(stage, stages, stage_file)
    if returncode != 0 or stderr:
        print(
            f"compare {stage.name}: ams-review exited {returncode}: "
            + stderr.decode(errors="replace").strip(),
            file=sys.stderr,
        )
        return False
    actual = _answers(
        stdout.decode("utf-8").splitlines(),
        (*outputs, *COLUMN_FIELDS, *stage.answer_checks),
        f"ams-review stage {stage.name}",
    )
    missing = [key for key in expected if key not in actual]
    extra = [key for key in actual if key not in expected]
    differing = []
    problems: list[tuple[str, str, str]] = []
    for key, fields in expected.items():
        answer = actual.get(key)
        if answer is None:
            continue
        for name in COLUMN_FIELDS if key.startswith("column:") else outputs:
            if name not in fields or name not in answer or not same(fields[name], answer[name]):
                differing.append((key, name))
        for name, check in stage.answer_checks.items():
            problem = check(init, fields, answer[name]) if name in answer else f"the answer has no {name}"
            if problem is not None:
                problems.append((key, name, problem))
    for label, keys in (("unanswered", missing), ("not in the dump", extra)):
        if keys:
            print(
                f"compare {stage.name}: {len(keys):,} keys {label}, the first {keys[:EXAMPLES_SHOWN]}",
                file=sys.stderr,
            )
    for key, name in differing[:EXAMPLES_SHOWN]:
        print(
            f"compare {stage.name}: {key} {name} differs "
            + _difference(expected[key].get(name), actual[key].get(name)),
            file=sys.stderr,
        )
    for key, name, problem in problems[:EXAMPLES_SHOWN]:
        print(f"compare {stage.name}: {key} {name}: {problem}", file=sys.stderr)
    ok = not (missing or extra or differing or problems)
    print(
        f"compare {stage.name} ({stages}): {len(expected):,} lines, {len(differing):,} differ, "
        + (f"{len(problems):,} fail their answer checks, " if stage.answer_checks else "")
        + f"{len(missing):,} unanswered, {len(extra):,} not in the dump"
        + ("; Rust reproduces every line" if ok else ""),
        file=sys.stderr,
    )
    return ok


UNICODE_TABLES = REPO_ROOT / "rebuild" / "review-rs" / "src" / "pyfmt" / "unicode_tables.rs"
# Each table's Rust name, the `str` predicate it holds, and what its doc comment says it is.
UNICODE_PREDICATES = (
    ("PRINTABLE", "isprintable", "`str.isprintable`"),
    ("SPACE", "isspace", "`str.isspace`, behind `strip`, `split` and `re`'s `\\s`"),
    ("DIGIT", "isdigit", "`str.isdigit`"),
    ("DECIMAL", "isdecimal", "`str.isdecimal`, the digits `int` reads in any script,"),
)


def _predicate_ranges(predicate: Callable[[str], bool]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = None
    for point in range(sys.maxunicode + 2):
        if point <= sys.maxunicode and predicate(chr(point)):
            if start is None:
                start = point
        elif start is not None:
            ranges.append((start, point - 1))
            start = None
    return ranges


def _check_decimal_runs(ranges: Sequence[tuple[int, int]]) -> None:
    """Raise ValueError unless every `str.isdecimal` range is whole runs of ten digits from zero, the premise `pyfmt::decimal_value` reads a digit's value from."""
    for start, end in ranges:
        for point in range(start, end + 1):
            if unicodedata.decimal(chr(point)) != (point - start) % 10:
                raise ValueError(f"U+{point:04X} breaks the runs of ten decimal digits from U+{start:04X}")


def unicode_tables_text() -> str:
    """The text of `UNICODE_TABLES` for this CPython: each predicate's inclusive code point ranges in ascending order, one per line under `#[rustfmt::skip]`."""
    lines = [
        "//! The pinned CPython's `str` predicates as code point ranges, for `repr` and the ported string handling ([`super::is_printable`], [`super::is_space`], [`super::is_digit`], [`super::decimal_value`]). `uv run python -m rebuild.tools.units_stage_fixtures unicode-tables` generates this file and `--check` fails when it is stale; it is never edited by hand.",
        "",
        "/// The Unicode version of the CPython the tables come from (`unicodedata.unidata_version`).",
        f'pub const UNICODE_VERSION: &str = "{unicodedata.unidata_version}";',
    ]
    for name, method, what in UNICODE_PREDICATES:
        ranges = _predicate_ranges(getattr(str, method))
        if method == "isdecimal":
            _check_decimal_runs(ranges)
        lines += [
            "",
            f"/// The code points {what} accepts, as inclusive ranges in ascending order.",
            "#[rustfmt::skip]",
            f"pub static {name}: &[(u32, u32)] = &[",
            *(f"    (0x{start:04X}, 0x{end:04X})," for start, end in ranges),
            "];",
        ]
    return "\n".join(lines) + "\n"


def unicode_tables(check: bool) -> bool:
    """Write `UNICODE_TABLES`, or with `check` report whether it holds what this CPython would write, and return whether it does."""
    text = unicode_tables_text()
    if not check:
        UNICODE_TABLES.write_text(text, encoding="utf-8")
        print(f"wrote {UNICODE_TABLES.relative_to(REPO_ROOT)}", file=sys.stderr)
        return True
    current = UNICODE_TABLES.read_text(encoding="utf-8") if UNICODE_TABLES.is_file() else None
    if current != text:
        print(
            f"{UNICODE_TABLES.relative_to(REPO_ROOT)} is not what this CPython (Unicode "
            f"{unicodedata.unidata_version}) gives; unicode-tables rewrites it",
            file=sys.stderr,
        )
        return False
    print(f"{UNICODE_TABLES.relative_to(REPO_ROOT)} is current", file=sys.stderr)
    return True


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", choices=SOURCES, required=True)
    parser.add_argument("--per-config", type=int, default=0, metavar="K")
    parser.add_argument("--batches", type=int, default=0, metavar="N")
    parser.add_argument("--all", action="store_true", dest="everything")


def _add_scenario_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scenario",
        choices=BUILD_SCENARIOS,
        help="take a build source: this scenario's one build, in place of the worker's handouts",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="units_stage_fixtures", description=(__doc__ or "").split("\n\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    dump_parser = commands.add_parser("dump", help="write the units file and the selected stages' lines")
    _add_source_arguments(dump_parser)
    _add_scenario_argument(dump_parser)
    dump_parser.add_argument("--stage", action="append", metavar="STAGE")
    dump_parser.add_argument("--out", type=Path, required=True)
    dump_parser.add_argument(
        "--every", type=int, metavar="N", help="a live build dump's boundary stages record every Nth unit"
    )
    check_parser = commands.add_parser("selfcheck", help="check that the wrappers change nothing")
    _add_source_arguments(check_parser)
    _add_scenario_argument(check_parser)
    compare_parser = commands.add_parser("compare", help="check the Rust side of a stage against a dump")
    compare_parser.add_argument("stage", metavar="STAGE")
    compare_parser.add_argument("--stages", type=Path, required=True, metavar="DIR")
    compare_parser.add_argument(
        "--fields", metavar="NAMES", help="check only these comma-separated output fields and field groups"
    )
    compare_parser.add_argument(
        "--write-threads", type=int, metavar="N", help="the width the write stage's Rust side writes at"
    )
    tables_parser = commands.add_parser("unicode-tables", help="write the crate's str predicate tables")
    tables_parser.add_argument("--check", action="store_true", help="fail when the tables are stale")
    prepare_parser = commands.add_parser("prepare", help="prepare build scenarios over a source's base prior")
    prepare_parser.add_argument("--source", choices=SOURCES, required=True)
    prepare_parser.add_argument("--scenario", choices=(*BUILD_SCENARIOS, "all"), required=True)
    prepare_parser.add_argument(
        "--check", action="store_true", help="run each scenario's Python builds and check what they reached"
    )
    child_parser = commands.add_parser("python-build", help="run one build a prepare request describes")
    child_parser.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    if args.command == "unicode-tables":
        return 0 if unicode_tables(args.check) else 1
    if args.command == "python-build":
        return python_build_child(args.request)
    if args.command == "prepare":
        names = scenario_names(args.source, args.scenario)
        for name in names:
            if args.source not in BUILD_SCENARIOS[name]:
                raise SystemExit(
                    f"the {name} scenario runs on {', '.join(BUILD_SCENARIOS[name])}, not {args.source}"
                )
        return 0 if prepare(args.source, names, args.check) else 1
    if args.command == "compare":
        if args.stage in BUILD_STAGES:
            (build_stage,) = _selected([args.stage], BUILD_STAGES)
            return (
                0
                if build_compare(
                    build_stage,
                    args.stages,
                    compared_fields(build_stage, args.fields),
                    args.write_threads,
                )
                else 1
            )
        if args.write_threads is not None:
            raise SystemExit(
                f"--write-threads sets the {WRITE_STAGE} stage's width, not the {args.stage} stage's"
            )
        (stage,) = _selected([args.stage], STAGES)
        return 0 if compare(stage, args.stages, compared_fields(stage, args.fields)) else 1
    if args.scenario is not None:
        if args.per_config or args.batches or args.everything:
            raise SystemExit(
                "--per-config, --batches and --all choose a worker source's handouts, not a build's"
            )
        if args.command == "dump":
            build_stages = _selected(args.stage, BUILD_STAGES)
            build_dump(open_build_dump(args.source, args.scenario, args.out, args.every), build_stages)
            return 0
        return 0 if build_selfcheck(args.source, args.scenario) else 1
    if getattr(args, "every", None) is not None:
        raise SystemExit("--every strides a build dump's boundary stages; it takes --scenario")
    stages = _selected(args.stage, STAGES) if args.command == "dump" else []
    source = open_source(args.source, args.per_config, args.batches, args.everything)
    if args.command == "dump":
        dump(source, stages, args.out)
        return 0
    return 0 if selfcheck(source) else 1


if __name__ == "__main__":
    raise SystemExit(main())
