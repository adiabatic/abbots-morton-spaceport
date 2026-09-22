# Compressed fold experiment

Issue #302 tests whether M1 can preserve deep-slot classes through the fold while producing the same ordered settlement rules, treaties, certificates, and compiled behavior as the production fold. The production path remains available throughout the experiment, and a measured negative result closes the experiment.

## Experiment shape

`rebuild/tools/compressed_fold_experiment.py` is the measurement authority. It runs from the isolated source clone under `var/keep/issue302/source`, keeps application caches under `var/keep/issue302/app-cache/`, and writes raw logs and self-describing JSON records under `var/keep/issue302/runs/` and `var/keep/issue302/ladder/`. Its child process mirrors the full M1 build through the table gates, Manual pins, and oracle. The parent reaps that child with `rebuild.tools.peak_rss`, so the wall and peak use the same units and process boundary as the repository's other performance harnesses.

Each run records the commit, tracked patch digest, kernel binary digest, table input stamp, machine, derived fan-outs, cache arm, command, wall, peak resident set, and artifact snapshot. The snapshot covers the settlement, treaty, and window tables; table digests; generated feature code and font; the build and gate summaries; and the compiled GSUB budget and its offset headroom. Window identity is taken over the uncompressed bytes so gzip container metadata cannot create a false divergence. The peak is `wait4`'s widest single process or descendant, not the sum of workers resident at the same time, so it compares like runs without claiming a complete process-tree memory total.

The two arms run in separate cache directories. `production` clears `AMS_COMPRESSED_FOLD`; `prototype` sets it. Cold runs require an empty named cache, warm runs reuse a completed cache without a source edit, and rune-edit runs reuse it after the controlled `qsPea` preference edit. `edit apply` changes the follower after `qsIt` from `qsEt` to `qsEight` in the isolated clone, records the before and after bytes and hashes, and `edit restore` reverses exactly that block. The edit is experimental input, never authored rationale.

`prepare` refreshes the shared old-font subset inputs before either arm is timed. The first diagnostic that creates those subsets is setup evidence rather than a paired cold observation. A production/prototype cold comparison starts only after both arms see the same fresh subset state; each child still records its baseline-preflight, outgoing-check, pipeline-and-gates, and whole-child walls separately. Here cold means an empty application cache with shared old-font inputs and a prebuilt kernel; the protocol does not flush the operating system's filesystem cache.

## Prototype representation

`CompressedRows` partitions each input's source incidence independently in the third and fourth right slots. Singleton boundary atoms and successor cuts in the second and third slots make the joint flag constant over each atom rectangle. Every atom retains its source seat, provenance, and sample order. The existing rule fold emits representative outcomes, unions whole atoms, and sorts the resulting concrete classes; the atom tables are dropped after grouping. Literal boundary classes retain `BOUNDARY_LOOKAHEAD_CLASS` order; only classes that actually expand atoms are sorted.

Certificates read a `VirtualRows` view instead of a resident Cartesian `FoldRow` vector. For one near prefix, the concrete third-slot members share one sorted fourth-slot and source-seat pattern when their active source rows are identical. `Prefixes` and first-match validation still visit every concrete row. Distance and parent arrays remain concrete-row indexed, so the prototype removes the expanded row records from residence without claiming that certificate construction has become symbolic.

Every existing fold assertion remains active. The global atoms do not make original deep-class union checks tautological, so the deep-union assertion stays. The evidence reports the remaining complete concrete visits and arrays explicitly rather than describing the prototype as eliminating all expansion.

## Commands

After the experiment code is committed, prepare an isolated local clone from the repository root. Copy only the old-font inputs needed by baseline preparation; do not copy live M1 artifacts, review output, or verdict state. Install dependencies and build the kernel before any timed run:

```zsh
git clone --shared --no-hardlinks . var/keep/issue302/source
mkdir -p var/keep/issue302/source/rebuild/out
cp site/*.otf var/keep/issue302/source/site/
cp rebuild/out/baseline-*.tsv.gz rebuild/out/baseline-font-projections.json var/keep/issue302/source/rebuild/out/
cd var/keep/issue302/source
uv sync
cargo build --release --manifest-path rebuild/kernel-rs/Cargo.toml
```

Then run the experiment commands from `var/keep/issue302/source`:

```zsh
uv run python -m rebuild.tools.compressed_fold_experiment prepare
uv run python -m rebuild.tools.compressed_fold_experiment run --arm production --scenario cold --cache production
uv run python -m rebuild.tools.compressed_fold_experiment run --arm production --scenario warm --cache production
uv run python -m rebuild.tools.compressed_fold_experiment edit apply
uv run python -m rebuild.tools.compressed_fold_experiment run --arm production --scenario rune-edit --cache production
uv run python -m rebuild.tools.compressed_fold_experiment edit restore
```

Repeat the sequence with `--arm prototype --cache prototype`. Alternate the arm order on repeated pairs. Use at least three paired repetitions when an apparent gain is close enough to run-to-run variation that one pair cannot distinguish it.

Run the repository's nested ladder once per arm after building the corresponding release binary:

```zsh
uv run python -m rebuild.tools.compressed_fold_experiment ladder --arm production
uv run python -m rebuild.tools.compressed_fold_experiment ladder --arm prototype
```

Compare complete application-cache artifacts after matching scenarios:

```zsh
uv run python -m rebuild.tools.compressed_fold_experiment compare --left ../app-cache/production --right ../app-cache/prototype --output ../comparisons/rune-edit.json
```

The comparison command answers artifact identity. Interpreting a timing pair also requires matching source, kernel binary, table input stamp, machine, derived widths, and scenario in the run records, plus the progression from cold to warm to rune-edit through the same application cache for each arm.

The comparison must be byte-identical for ordered settlement rules and treaties in every settlement configuration, the complete window enumeration after decompression, and the generated feature code. It records the compiled font's raw digest, compares the font through `fingerprint.font_content_digest`'s head- and name-blind projection, and compares the read-back GSUB budget and offset headroom. Gate summaries remain raw evidence but do not fail identity merely because cache or timing fields differ. Any rule-order or behavior divergence blocks adoption. A certificate may have different spelling only after an additional comparison proves the same coverage with no weaker independently settled witness; the default byte comparison deliberately reports such a change.

## Verification and decision

The prototype exercises the boundary fallbacks, explicit ZWNJ handling, identity guards, guarded ligature formation, both deep slots, and partially overlapping classes in hermetic crate fixtures. It retains the negative cases for omitted windows, incorrect successor pins, class-member disagreement, and first-match ordering. Full-alphabet acceptance also requires the kernel gate, the font and rebuild suites, an M1 build, and an artifact-cycle rehearsal in the isolated clone with its review output redirected. Heavy gates run serially according to `doc/parallelism.md` and detach according to `doc/running-long-steps.md`.

The isolated prototype rehearsal needs no verdict source and writes no verdict store:

```zsh
AMS_COMPRESSED_FOLD=1 make artifact-cycle ARGS='--review-out var/rehearsal-review --no-carry --no-merge --fresh'
```

An oracle-unmatched case that also occurs on the production arm remains diagnostic evidence and is not attributed to the prototype without an arm delta. The final decision waits for the paired runs and gates rather than drawing a conclusion from that pre-existing diagnostic alone.

The issue body points to the kept evidence and records the recommendation. Adoption requires identical required output and coverage plus a meaningful whole-build or memory improvement without reduced fan-out or disproportionate complexity. The recommendation names every retained full expansion and identifies the exact production transformation or assertion a follow-up may remove and the invariant that replaces it.
