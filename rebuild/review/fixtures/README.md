# Review-surface contract fixture

A hand-written miniature of a `build_m1` surface — small enough to hold in one head, stable enough to assert against. The units under `units/` are _source_, not build output: no run of `rebuild.review.build` produces them, and a byte-faithful regeneration through the real builder is impossible by design. Enrichment wants live M1 artifacts, fonts, and subset TSVs; the provenance stamps (`generated_at`, `repo_head`, `inputs_fingerprint`) are not injectable; and the stub summaries, round-number highlight geometry, and empty provenance these units carry are not shapes the real `Enricher` emits. What _is_ regenerable-shaped is the front half of the pipeline, and `fixture-audit.tsv` and `fixture-ledger.yaml` are it: the real ingestion formats, written out honestly, so the shards' windows, classes, and counts have something to be checked against.

## What binds it

- `check_manifest`, `check_unit`, and `check_shards` in `rebuild/review/build.py` — the §7 contract checker, run over this directory by `rebuild/test_review_build.py` and `rebuild/test_surface_checks.py`. `check_shards` is the half that gates every real build: `check_unit` over the units that build computed, a cache-served unit held instead by the `content_key` stamp its shard and its store record must agree on, and the cross-unit predicates over both. `check_manifest` and the predicates about the files beside the manifest reach a real build through `check_output_dir` instead, which the contracts lane holds to an empty error list over the mini bundle and over a table diff; this directory carries no fonts, index page or sidecars, so what runs over it directly is `check_manifest` and `check_shards`.
- `test_fixture_sources_derive_the_checked_in_shards` in that module: `audit.load_workload` over the two sources must reproduce the shards' windows, classes, kinds, and configs, plus the manifest's per-class and total counts. This is what makes the manifest's `row_count`s and `totals.rows` checkable at all — `check_shards` only compares them against each other, never against rows.
- Each unit's `content_key`: a byte-identity contract with every prior surface snapshot, checked by `rebuild/test_carry_verdicts.py`, with `carry_content_hash` in `rebuild/review/unit_cache.py` as the authority that computes it. `picture_identical` is a carry-presentation key (`CARRY_PRESENTATION_KEYS` there), so flipping it on a unit never moves its stamp.
- `test_fixture_units_exercise_the_contract_branches`, which names the branches these units exist to cover. Keep them covered when the fixture grows. One of them is the slim shape: a unit that takes no verdict (`audit.slim_fragment`) omits `explain`, `drafts` and `highlight` outright, and its `content_key` is the hash of what it carries.

## `mini/` — the frozen mini-M1 bundle

A second, quite different fixture living beside this one: not a hand-written miniature but a **slice of real build output**, frozen so that tests about the build machinery need no live `rebuild/out/`. It holds `audit.tsv` — the divergence audit filtered twice over, keeping every window drawn from ·Pea, ·Tea, ·Day, ·Roe and the boundary tokens, plus every window named in `regenerate.EXAMPLE_WINDOWS`, which is the set the review surface's worked examples name by codepoint — the matching `baseline-<config>.subset.tsv.gz` slice for each acceptance config and no other, `M1.otf`, and the default settlement and treaty tables.

What it lets run against frozen output, at full width, instead of against the live `rebuild/out/`:

- the whole of `rebuild/test_unit_cache.py` (a mini `build_m1` costs seconds), and the mini build `rebuild/test_app_index.py` holds the shipped sidecars against
- the ordering and dedupe properties in `rebuild/test_review_audit.py`, and `test_assignment_is_deterministic` in `rebuild/test_review_families.py`
- the enrich and drafts worked examples, through the `example_units` fixture in `rebuild/conftest.py` — which position the enricher judges, how the drafter words a record — over the frozen exemplar windows
- the ink comparisons in `rebuild/test_review_ink.py`, over the bundle's font and a stride through its workload
- the table-diff build, the snapshot round trip, and the two witness arms in `rebuild/test_review_tablediff.py`, which re-settle the frozen tables under the very spec they were built from
- the manual-pin gate's teeth, which need a font and a spec that agree with each other rather than the live pair

A worked-example window that stops selecting any audit row fails the regeneration by name, which is where a dissolved exemplar should be found — not in a red suite a rune edit later.

It also holds `pin.json`: the tree and blob shas of `glyph_data/runes`, `rebuild/schema`, `rebuild/script.yaml` and `rebuild/m1-divergences.yaml` at the commit the bundle was regenerated on, with `pin.PINNED_PATHS` in `mini/pin.py` as the authority on that list. The `mini_bundle` fixture in `rebuild/conftest.py` materializes those objects out of git into a session temp directory, and every mini-bundle test hands that directory to `build_m1` as its `spec_root` and reads its ledger from there — so the settlement the enricher re-derives is the one these rows were written under, a rune edit cannot redden the contracts lane, and there is no second copy of the runes in the tree to edit by mistake. The pin is content-addressed, so a rebase that leaves those files' bytes alone keeps it valid, and a pin whose objects this repository no longer holds fails loudly and names the command that regenerates the bundle. Everything else in a mini build still comes from the repo root — the fingerprints, the git head, the manifest's relative paths, the corpus the pin drafts validate against — because those are facts about the checkout rather than about the workload.

All of it moves together and only together — a subset slice from one build beside a font from another, or a pin from a third, would have the enricher reporting glyph disagreements that are the bundle's fault. Regeneration refuses a spec with uncommitted edits, since the pin names committed objects, and a live build that was not made from the tree as it stands. Regenerate the whole of it at once, after a fresh `run_m1`:

```zsh
uv run python rebuild/review/fixtures/mini/regenerate.py
```

That script is the authority on what the bundle holds and how the filter is drawn.

## Growing it

1. Add the window's rows to `fixture-audit.tsv`, one per (unit, config). Rows are grouped by unit rather than by config — `load_audit` is order-blind, and contiguity is what a hand-editor wants. The dedupe key is (`codepoints`, `baseline`, `new`), so one unit's rows share a triple and no two units may share one.
2. For a new class, add an entry to `fixture-ledger.yaml` and a matching entry to the manifest's `classes`; the two mirror each other field for field, in the same order.
3. Hand-write the unit into its class shard under `units/`, in the builder's emission shape — copy the nearest neighbor and change what differs. A fragment carries no `batch`: whether it takes a verdict is its flags, and where it sits in the queue is the manifest's `human_unit_ids`.
4. Stamp the unit's `content_key` with `carry_content_hash` applied to the unit with its `content_key` key removed, and give it the id `unit_id_for` derives from that stamp; keep each shard sorted by id.
5. Update `unit_count`, `row_count`, `machine_approved_count`, `human_unit_ids` (the human units in `audit.triage_key` order — manifest class order, group, window, id), each class's `batches`, and `totals` in `manifest.json`.
6. Run `uv run pytest rebuild/test_review_build.py rebuild/test_carry_verdicts.py -n auto --dist worksteal`.
