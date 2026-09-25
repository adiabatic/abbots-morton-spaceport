# Review-surface contract fixture

A small hand-written `build_m1` surface for tests to assert against. The units under `units/` are source files: no run of `rebuild.review.build` produces them, and the real builder cannot regenerate them. Enrichment needs live M1 artifacts, fonts, and subset TSVs, the provenance stamps (`generated_at`, `repo_head`, `inputs_fingerprint`) cannot be injected, and these units carry stub summaries, round-number highlight geometry, and mostly empty provenance that the real `Enricher` does not emit. `fixture-audit.tsv` and `fixture-ledger.yaml` are the pipeline's inputs in the real ingestion formats, so the shards' windows, classes, and counts can be checked against them.

## What binds it

- `check_manifest`, `check_unit`, and `check_shards` in `rebuild/review/build.py`, the §7 contract checker, which `rebuild/test_review_build.py` and `rebuild/test_surface_checks.py` run over this directory. Every real build runs `check_unit` over the units it computes and the cross-unit predicates of `check_shards` over all its units; a cache-served unit skips `check_unit`. Its shard and its store record must agree on its `content_key` stamp, which covers every field outside `unit_cache.CARRY_PRESENTATION_KEYS`. The `echo`, `cluster`, and `secondary_seams` fields that the write patches again are outside the stamp, so on a served unit only the cross-unit predicates check them. `check_manifest` and the predicates on the files beside the manifest reach a real build through `check_output_dir`, which the contracts lane runs over the mini bundle's build and over a table diff and expects to return no errors. This directory has no fonts, index page, or sidecars, so only `check_manifest` and `check_shards` run over it directly.
- `test_fixture_sources_derive_the_checked_in_shards` in `rebuild/test_review_build.py`: `audit.load_workload` over the two sources must reproduce the shards' windows, classes, kinds, and configs, and the manifest's per-class and total counts. It is the only check of the manifest's `row_count`s and `totals.rows` against rows, because `check_shards` compares them only with each other.
- Each unit's `content_key`, from which the unit's id derives, so it must stay byte-identical for every recorded verdict to find its unit. `rebuild/test_carry_verdicts.py` checks it, and `carry_content_hash` in `rebuild/review/unit_cache.py` computes it. `picture_identical` is in `CARRY_PRESENTATION_KEYS` there, so changing it on a unit does not change the stamp.
- `test_fixture_units_exercise_the_contract_branches`, which names the branches these units cover. Keep them covered when the fixture grows. One is the slim shape: a unit that takes no verdict (`audit.slim_fragment`) omits `explain`, `drafts`, and `highlight`, and its `content_key` is the hash of what it carries.

## `mini/` — the frozen mini-M1 bundle

A second fixture of a different kind: a slice of real build output, frozen so that tests of the build machinery need no live `rebuild/out/`. It holds:

- `audit.tsv`, the divergence audit filtered to every window drawn from ·Pea, ·Tea, ·Day, ·Roe, and the boundary tokens, plus every window in `regenerate.EXAMPLE_WINDOWS`, the windows the review surface's worked examples name by codepoint
- a `baseline-<config>.subset.tsv.gz` slice for each acceptance config and no other
- `M1.otf`, and the default settlement and treaty tables

These tests run against it at full xdist width instead of against the live `rebuild/out/`:

- all of `rebuild/test_unit_cache.py` (a mini `build_m1` takes seconds), and the mini build that `rebuild/test_app_index.py` checks the sidecars against
- the ordering and dedupe properties in `rebuild/test_review_audit.py`, and `test_assignment_is_deterministic` in `rebuild/test_review_families.py`
- the enrich and drafts worked examples, through the `example_units` fixture in `rebuild/conftest.py`: which position the enricher judges and how the drafter words a record, over the frozen example windows
- the ink comparisons in `rebuild/test_review_ink.py`, over the bundle's font and a stride through its workload
- the table-diff build, the snapshot round trip, and the two witness tests in `rebuild/test_review_tablediff.py`, which re-settle the frozen tables under the spec they were built from
- the failing-pin tests of the manual-pin gate (`TestTeeth` in `rebuild/test_manual_pins.py`), which need a font and a spec that match each other

When a worked-example window stops selecting any audit row, regeneration fails and names it, so the lost example is found there and not in a test failure after a later rune edit.

It also holds `pin.json`: the tree and blob shas of the paths in `pin.PINNED_PATHS` (`mini/pin.py`) at the commit the bundle was regenerated on. The `mini_bundle` fixture in `rebuild/conftest.py` writes those objects out of git into a session temp directory, and every mini-bundle test passes that directory to `build_m1` as its `spec_root` and reads its ledger from there. So the enricher re-derives the settlement these rows were written under, a rune edit cannot fail the contracts lane, and there is no second copy of the runes in the tree to edit by mistake. The pin is content-addressed, so a rebase that leaves those files' bytes unchanged keeps it valid. A pin whose objects the repository no longer holds fails and names the command that regenerates the bundle. Everything else in a mini build comes from the repo root (the fingerprints, the git head, the manifest's relative paths, the corpus the pin drafts are validated against), because those describe the checkout and not the workload.

When the pinned schema has no `outgoing` stance property, `pin._preserve_authored_outgoing` adds one that allows only an exception, and declares that exception on every stance of every frozen ligature rune. It takes no rules from the working tree, and a pinned schema that already has `outgoing` is left unchanged. This keeps the frozen font and tables paired with the behavior their runes declared, under the current loader.

All of the bundle must be regenerated together. A subset slice from one build beside a font from another, or a pin from a third, would make the enricher report glyph disagreements caused by the bundle. Regeneration fails when the pinned paths have uncommitted edits, since the pin names committed objects, and when the live build was not made from the tree as it stands. After a fresh `run_m1`, run:

```zsh
uv run python rebuild/review/fixtures/mini/regenerate.py
```

That script defines what the bundle holds and how the filter is drawn.

## Growing it

1. Add the window's rows to `fixture-audit.tsv`, one per (unit, config). Group the rows by unit for ease of editing; `load_audit` ignores row order. The dedupe key is (`codepoints`, `baseline`, `new`), so one unit's rows share a triple and no two units may share one.
2. For a new class, add an entry to `fixture-ledger.yaml` and a matching entry to the manifest's `classes`, with the same fields in the same order.
3. Write the unit into its class shard under `units/` in the builder's output shape by copying the nearest unit and changing what differs. A fragment carries no `batch`: its flags say whether it takes a verdict, and the manifest's `human_unit_ids` gives its place in the queue.
4. Set the unit's `content_key` to `carry_content_hash` of the unit without its `content_key` key, and give it the id `unit_id_for` derives from that key. Keep each shard sorted by id.
5. Update `unit_count`, `row_count`, `machine_approved_count`, `human_unit_ids` (the human units in `audit.triage_key` order: manifest class order, group, window, id), each class's `batches`, and `totals` in `manifest.json`.
6. Run `uv run pytest rebuild/test_review_build.py rebuild/test_carry_verdicts.py -n auto --dist worksteal`.
