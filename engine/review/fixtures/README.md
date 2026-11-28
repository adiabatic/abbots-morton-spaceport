# Review-corpus contract fixture

A small hand-written table-diff corpus that the Python contract tests and the review app's node tests (`engine/review/jstests/render.test.js`, `slim.test.js` and `queue.test.js`) assert against. The units under `units/` are source files: no run of `engine.review.build` produces them. A real build needs a snapshot of an accepted build, a build and both fonts, the provenance stamps (`generated_at`, `repo_head`, `inputs_fingerprint`) cannot be injected, and these units carry stub summaries, round-number highlight geometry and short provenance that `diff_units.fragment` does not emit. The manifest's `row_count`s and `totals.rows`, which `check_shards` compares only with each other, are set by hand and checked against no rows.

## What binds it

- `check_manifest`, `check_unit` and `check_shards` in `engine/review/build.py`, the §7 contract checker, which `engine/test_review_build.py` and `engine/test_corpus_checks.py` run over this directory. Every build runs them over what it writes, and `check_output_dir` runs them with the predicates on the files beside the manifest over a written corpus, a real table-diff build among them in `engine/test_review_diff_units.py`. This directory has no fonts, index page or sidecars, so only `check_manifest` and `check_shards` run over it directly.
- Each unit's `content_key`, from which its id derives (`unit_ids.unit_id_for`). A fixture unit's key is `restamp.fixture_identity`, the sha256 of the fields of `diff_units.unit_identity` that a hand-written unit carries, so editing one of them gives the unit a new id.
- `test_fixture_units_exercise_the_contract_branches` in `engine/test_review_build.py`, which names the branches these units cover. Keep them covered when the fixture grows.
- The node tests, which load every shard the manifest lists, in manifest order. Their `the fixtures must …` assertions name the branches they need beyond the contract's, and their other assertions name unit ids, class ids, titles and totals, so growing the fixture means updating those literals. `restamp.py` keeps the unit ids among them current.
- `engine/test_app_index.py` and `engine/test_corpus_checks.py` read `units/` as one `units/<class id>.json` per manifest class, so nothing else goes there.

## `mini/` — the frozen mini bundle

A second fixture of a different kind: a slice of real build output, frozen so that tests of the build machinery need no live `engine/out/`. The module docstring of `mini/regenerate.py` says what the bundle holds and what it checks before it freezes the live build's font.

Every test that needs a real font reads the bundle's instead of the live `engine/out/`, so it runs at full xdist width. Each one reaches the bundle through its own path to `engine/review/fixtures/mini/`, so this search lists every test module that reads it:

```zsh
git grep -l -E '/ "mini"|fixtures/mini' -- 'engine/test_*.py' 'engine/review/jstests/*.test.js'
```

After a fresh `run_build`, regenerate the whole bundle with:

```zsh
uv run python engine/review/fixtures/mini/regenerate.py
```

## Growing it

1. Write the unit into its class's shard under `units/` in the shape `diff_units.fragment` writes, every key in its order, by copying the nearest unit and changing what differs. Its `class` is the one its kinds give it (`check_unit` holds a join row to `joins`, a redrawn glyph to `glyphs` and a kern pair to `kerning`), `no_verdict` is true exactly in `kerning` and `provenance-only`, and a unit that takes a verdict carries a `cluster` while one that takes none carries null. A fragment carries no `batch`: its flags say whether it takes a verdict, and the manifest's `human_unit_ids` gives its place in the queue.
2. Give it a placeholder id that no other file in the repo contains.
3. Set its class's `row_count` in `manifest.json`, and for a class the fixture does not hold yet, add its entry to `classes` in section order, then `diff_units.CLASS_IDS` order.
4. Run `uv run python engine/review/fixtures/restamp.py`, which stamps every unit's `content_key`, replaces each placeholder with its id everywhere, keeps the shards in id order and recomputes `human_unit_ids`, each class's `batches` and counts and the totals; its docstring lists everything it rewrites.
5. Run `make test-engine`, which reruns every test that read a changed fixture file, and `node --test engine/review/jstests/*.test.js`.

Editing an existing unit's kinds, row key, configurations, code points or either run's glyphs changes its `content_key` and so its id. Run steps 4 and 5 after any such edit.

## `ink/before.otf` — the ink comparator's other font

The font `engine/test_review_ink.py` compares the mini bundle's font with: the Senior that `make all` built at commit `d23c0a87`, subset to the code points those tests shape (·Pea, ·Tea, ·Day, ·Roe, ·May, ·Oy, ·Low, ·Fee, ·Eight, the space, ZWNJ and the middle dot) with every layout feature kept. Over those code points it shapes every text of one to four characters as the whole font does, in every acceptance configuration (`conform.ACCEPTANCE_CONFIGS`), kerned and not. The tests' docstrings name the differences between it and the mini bundle's font that their readings rest on, so it is never rebuilt, and a regenerated mini bundle can need those readings restated.

## `verdict-record-samples.json` — the verdict record rule

A third fixture, unrelated to the corpus: verdict records, each with the answer `engine.review.verdict_store.well_formed` gives it. `engine/test_review_autosave.py` checks each answer against that function, and `engine/review/jstests/verdicts.test.js` against the review app's strict Import (`importVerdicts` with `strict`), which mirrors the rule in JavaScript, so none of them can drift apart. A change to the rule adds the samples that show it.

## `delta-set-samples.json` — the delta set rule

Also unrelated to the corpus: autosave delta sets, each with whether `engine.review.verdict_store.parse_delta_payload` takes a delta holding it, a rule that is not the record rule above. `engine/test_review_autosave.py` checks each answer against that function, and `engine/review/jstests/verdicts.test.js` against the review app's `wellFormedSet`, which mirrors the rule in JavaScript for the outbox replay (`planOutboxReplay`), so the two cannot drift apart. A change to the rule adds the samples that show it.

## `kern-inventory.json` and `kern-values.json` — the kerning view

`kern-inventory.json` is a slice of a built kern inventory (`engine/pipeline/kern_inventory.py`) to the rune pairs among ·No, ·Tea and ·It, with every glyph, side, facing pair, joined group and text their entries and witnesses name; a facing pair that only a kept text's other gaps name keeps its member and loses its witness. `kern-values.json` is an answer of the review server's `GET /kern` (`engine/review/kern_routes.py`) over it, written by hand: the rune files set flipped ·No before half ·Tea to −1, by that facing pair's own record, and the built font has loop ·No before full ·Tea at −1. The kerning view's node tests (`engine/review/jstests/kern.test.js` and `kern-draw.test.js`) read both and name those pairs, `engine/test_kern_inventory.py` holds the first to the inventory's shape (`kern_inventory.check_document`), and `engine/test_review_kern_routes.py` holds the second to the answer's (`kern_routes.check_values_payload`).
