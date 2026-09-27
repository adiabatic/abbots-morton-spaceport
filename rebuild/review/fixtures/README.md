# Review-corpus contract fixture

A small hand-written `build_m1` corpus for tests to assert against. The units under `units/` are source files: no run of `rebuild.review.build` produces them, and the real builder cannot regenerate them. Enrichment needs live M1 artifacts, fonts, and subset TSVs, the provenance stamps (`generated_at`, `repo_head`, `inputs_fingerprint`) cannot be injected, and these units carry stub summaries, round-number highlight geometry, and mostly empty provenance that the real `Enricher` does not emit. `fixture-audit.tsv` and `fixture-ledger.yaml` are the pipeline's inputs in the real ingestion formats, so the shards' windows, classes, and counts can be checked against them.

## What binds it

- `check_manifest`, `check_unit`, and `check_shards` in `rebuild/review/build.py`, the §7 contract checker, which `rebuild/test_review_build.py` and `rebuild/test_corpus_checks.py` run over this directory. Every real build runs `check_unit` over the units it computes and the cross-unit predicates of `check_shards` over all its units; a cached unit skips `check_unit`. Its shard and its store record must agree on its `content_key` stamp, which covers every field outside `unit_cache.CARRY_PRESENTATION_KEYS`. The `duplicate_group`, `cluster`, and `secondary_junctions` fields that the write patches again are outside the stamp, so on a cached unit only the cross-unit predicates check them. `check_manifest` and the predicates on the files beside the manifest reach a real build through `check_output_dir`, which the contracts lane runs over the mini bundle's build and over a table diff and expects to return no errors. This directory has no fonts, index page, or sidecars, so only `check_manifest` and `check_shards` run over it directly.
- `test_fixture_sources_derive_the_checked_in_shards` in `rebuild/test_review_build.py`: `audit.load_workload` over the two sources must reproduce the shards' windows, classes, kinds, and configs, and the manifest's per-class and total counts. It is the only check of the manifest's `row_count`s and `totals.rows` against rows, because `check_shards` compares them only with each other.
- Each unit's `content_key`, from which the unit's id derives, so it must stay byte-identical for every recorded verdict to find its unit. `rebuild/test_carry_verdicts.py` checks it, and `carry_content_hash` in `rebuild/review/unit_cache.py` computes it. `picture_identical` is in `CARRY_PRESENTATION_KEYS` there, so changing it on a unit does not change the stamp.
- `test_fixture_units_exercise_the_contract_branches`, which names the branches these units cover. Keep them covered when the fixture grows. One is the slim shape: a unit that takes no verdict (`audit.slim_fragment`) omits `explain`, `drafts`, and `highlight`, and its `content_key` is the hash of what it carries.

## `mini/` — the frozen mini-M1 bundle

A second fixture of a different kind: a slice of real build output, frozen so that tests of the build machinery need no live `rebuild/out/`. The module docstring of `mini/regenerate.py` says what the bundle holds, how its windows are chosen, why all of it is regenerated together, and how `pin.json` pins the spec its rows settled under. The docstrings in `mini/pin.py` say how `materialize` writes that spec out of git and how `_preserve_authored_outgoing` adapts a pinned schema to the current loader.

Every test that needs real build output reads the bundle instead of the live `rebuild/out/`, so it runs at full xdist width. The readers are tests that build a corpus from the bundle (a mini `build_m1` takes seconds), tests that read its frozen audit rows and tables, and tests that need a real font, or a font and a spec that match each other. Each one reaches the bundle through the `mini_bundle`, `mini_corpus`, or `example_units` fixture in `rebuild/conftest.py` or through its own path to `rebuild/review/fixtures/mini/`, so this search lists every test module that reads it:

```zsh
git grep -l -E '/ "mini"|fixtures/mini|mini_bundle|mini_corpus|example_units' -- 'rebuild/test_*.py'
```

After a fresh `run_m1`, regenerate the whole bundle with:

```zsh
uv run python rebuild/review/fixtures/mini/regenerate.py
```

## Growing it

1. Add the window's rows to `fixture-audit.tsv`, one per (unit, config). Group the rows by unit for ease of editing; `load_audit` ignores row order. The dedupe key is (`codepoints`, `baseline`, `new`), so one unit's rows share a triple and no two units may share one.
2. For a new class, add an entry to `fixture-ledger.yaml` and a matching entry to the manifest's `classes`, with the same fields in the same order.
3. Write the unit into its class shard under `units/` in the builder's output shape by copying the nearest unit and changing what differs. A fragment carries no `batch`: its flags say whether it takes a verdict, and the manifest's `human_unit_ids` gives its place in the queue.
4. Set the unit's `content_key` to `carry_content_hash` of the unit without its `content_key` key, and give it the id `unit_id_for` derives from that key. Keep each shard sorted by id.
5. Update `unit_count`, `row_count`, `machine_approved_count`, `human_unit_ids` (the human units in `audit.triage_key` order: manifest class order, group, window, id), each class's `batches`, and `totals` in `manifest.json`.
6. Run `uv run pytest rebuild/test_review_build.py rebuild/test_carry_verdicts.py -n auto --dist worksteal`.
