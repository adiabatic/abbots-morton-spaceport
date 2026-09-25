# Baseline extraction plan (§13.1)

This plan specifies the baseline that `doc/rebuild-design.md` §13 (item 1) calls for. The extractor shapes every string in the depth-2 basis through the built Senior Sans font (`site/AbbotsMortonSpaceportSansSenior-Regular.otf`), treating the font as a black box, and records each string's outcome as a diff-stable table under `rebuild/out/`. The M1 oracle and the review surface read these tables as the old font's behavior. Related design sections: `doc/rebuild-design.md` §3.4, §6.1, §10, and §13.

The tables' headers record this provenance. The exception is the `ss03+ss05` table, extracted later, whose header records repo SHA `0cd71c4`:

| Fact         | Value                                                              |
| ------------ | ------------------------------------------------------------------ |
| Repo SHA     | `ae9d08d`                                                          |
| Font         | `site/AbbotsMortonSpaceportSansSenior-Regular.otf`                 |
| Font SHA-256 | `3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35` |

## 1. The basis

### Alphabet

47 symbols. `rebuild/baseline/alphabet.py` asserts the count.

- The 44 Quikscript runes, U+E650–U+E66C and U+E670–U+E67E (`doc/glyph-names.md`). The angle parentheses U+E66E and U+E66F are punctuation and are excluded.
- `space` (U+0020) and ZWNJ (U+200C), the boundary tokens that split runs.
- The namer dot, `periodcentered` (U+00B7). Letters condition on it: `qsExcite.exit_baseline_before_vertical` excludes it in `not_after` and ·Utter does not, which is the distinction §3.4 describes. Its own form also depends on context, because `periodcentered.lowered` replaces it at the start of a word whose first letter is Short. §3.4 makes it a boundary token that does not split runs, so it belongs inside strings and not only at their edges.

### Input set

Every string of length 1 through 4 over the 47-symbol alphabet. Each string is shaped in its own HarfBuzz buffer, so its start and end are run edges.

Row counts per configuration:

| Length | Count           |
| ------ | --------------- |
| 1      | 47              |
| 2      | 47² = 2,209     |
| 3      | 47³ = 103,823   |
| 4      | 47⁴ = 4,879,681 |
| Total  | **4,985,760**   |

Across the §5 configurations: **4,985,760 × |`rowmodel.CONFIGS`| rows**.

### Why this realizes the §6.1 windows

A settlement window is the resolved left neighbor, the letter itself, and up to four raw letters to its right (`doc/rebuild-design.md` §10). Black-box extraction cannot set the resolved left state directly, so the string prefix induces it. In a length-4 string, the letter at position 2 has a resolved left induced by a one-symbol prefix (every state reachable in one step from a run edge) and two raw-right symbols. Position 1 of every string gives every run-initial window (resolved left = edge) with up to three raw-right symbols. Strings with a space or ZWNJ inside give word-final and word-initial windows mid-string: `qsMay space qsTea qsKey` gives word-final ·May and word-initial ·Tea with lookahead in one row. Every position of every string is recorded, aligned by cluster, so later positions also contribute windows, with fewer raw-right symbols.

### What depth-2 cannot capture, accepted by design

- **Resolved left states that need two or more settled joins, with full lookahead.** Such a state first appears at position 3, where a length-4 string leaves at most one raw-right symbol. The basis supplies a third raw-right symbol only at position 1 and never a fourth. The baseline makes no completeness claim for these windows. For M1, the witness stage settles a certificate text of any length for every emitted rule, and the deep sweep and the deep replay check texts of length 5 and more (`doc/rebuild-design.md` §10).
- **Longer-range emergent effects**, such as the depth-5-only regressions the archive documents (`doc/rebuild-design.md` §15, item 11). The deep sweep (`make conform-deep`) checks depth 5 and beyond.
- **No window-keyed deduplication.** Keying rows by window instead of by string would assume the locality that this baseline exists to measure: that context beyond the window does not matter, and that boundary tokens behave like run edges. Full enumeration is cheap enough that deduplication is not needed.

## 2. Runtime and size strategy

- **Parallelism.** `extract.SHARD_WORKERS_DEFAULT` is the number of cores this process may run on (`memory_budget.usable_cores`, which reads the affinity mask and any cgroup CPU quota). It is not derived from a memory budget, because a shard worker's peak memory has not been measured. `--workers` overrides it. At more than one worker, each worker is a `spawn` process that builds its own `Shaper` and `SeamClassifier`. The shaper reuses one `hb.Buffer` and copies `glyph_infos` and `glyph_positions` out before the next shape. Work is sharded by (length, first symbol), 47 shards per length.
- **Determinism under parallelism.** Each worker writes its shard to a temporary file, and the writer concatenates the shards in shard-key order. Within a shard, rows are generated in the canonical row order (§3). The output bytes therefore do not depend on scheduling or on the worker count; `rebuild/test_extractor.py::test_small_extraction_is_deterministic` extracts at two workers and at one and compares the bytes. Two extractions at the same commit on the same font produce byte-identical uncompressed streams. The header records the commit, so extractions at different commits differ in that line.
- **Sizes.** A configuration's uncompressed table is hundreds of megabytes, so every table is written gzipped (`.tsv.gz`, with `mtime=0` so the gzip bytes are deterministic too). SHA-256 digests are computed over the uncompressed stream, header included.
- **`rebuild/out/` is gitignored.** Besides the tables, the extractor writes small, regenerable summaries that a later re-extraction can be compared against without the bulk files. `digest-<config>.json` holds a configuration's row count, the SHA-256 of its uncompressed stream, its seam counts per `y0/y5/y6/y8/lig/break`, and its full resolved-glyph-name frequency table. `digests.tsv` holds the row count, SHA-256, seam counts, and subset of every configuration that has a digest file. The `summarize` subcommand writes `SUMMARY.md`: the provenance, the symbol legend, the digest table, and each configuration's most frequent glyph names with its distinct-name count.

## 3. The table schema

One file per configuration: `rebuild/out/baseline-<config>.tsv.gz`, where `<config>` is one of the tokens in `rebuild/validation/rowmodel.CONFIGS`. One row per input string, with no deduplication (§1). Tab-separated, UTF-8, `\n` line endings.

`model.render_header` writes these header lines, each beginning with `#` and a space, in this order:

```text
# baseline-extract v<tool version>
# git_sha: <short repo SHA at extraction>
# font: site/AbbotsMortonSpaceportSansSenior-Regular.otf
# font_sha256: <SHA-256 of the font>
# config: <config token> (<enabled features, e.g. ss02=1 ss03=1; empty for default>)
# subset: <limit=N, or sample=N modulus=M; smoke runs only>
# alphabet_sha256: <SHA-256 of the newline-joined sorted codepoint list>
# columns: codepoints glyphs clusters seams positions
```

The `subset` line appears only in a smoke run (`--limit` or `--sample`), so a partial table cannot be mistaken for a full one.

Columns:

| Column       | Content                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `codepoints` | The input string as colon-joined uppercase hex codepoints, e.g. `E665:0020:E652:00B7`. The symbol legend is in `SUMMARY.md`.                                                                                                                                                                                                                                                                                                                                                                                                            |
| `glyphs`     | Resolved **full** glyph names in output order, pipe-joined, e.g. `qsMay.en-y0.ex-y5\|qsTea.half`. Names come from `TTFont.getGlyphName(gid)`, because HarfBuzz's `glyph_to_string` truncates names to 63 bytes.                                                                                                                                                                                                                                                                                                                         |
| `clusters`   | Comma-joined cluster index per output glyph: the earliest input position the glyph covers. A ligature is one glyph covering two input positions. Derive per-position names from `glyphs` and `clusters`, not from index arithmetic.                                                                                                                                                                                                                                                                                                     |
| `seams`      | One token per input seam (positions k and k+1, for k = 0 … len−2), comma-joined. The token is `lig` when no output glyph starts at position k+1, because a ligature consumed the seam. Otherwise it is the §4 classification of the flanking output glyphs: `y0`, `y5`, `y6`, or `y8` for a join at that pixel height, or `break` for no join. If more than one height matched, the heights are `+`-joined in ascending order (e.g. `y0+y5`), and the extraction raises an assertion after writing the table. No such seam is expected. |
| `positions`  | `x_offset,y_offset,x_advance` per output glyph in font units, pipe-joined, e.g. `0,0,350\|0,250,250`. This records cursive-attachment offsets and advances, so a later comparison can detect extension changes (which also appear as `ex-ext-N` name changes), kerning changes, and attachment shifts. `y_advance` is omitted, and the shaper raises an assertion if it is ever nonzero.                                                                                                                                                |

Row order: by string length, then by codepoint tuple, both ascending (`model.row_sort_key`). Every value is an integer or a name; there is no floating point. The same font, tool version, and commit give byte-identical uncompressed output.

## 4. Seam classification

`SeamClassifier` reads join heights from the built font's GPOS:

1. At start-up it collects the lookups that the `curs` feature references, so no lookup index is hardcoded. For each lookup it records the glyphs with an ExitAnchor and the glyphs with an EntryAnchor. It asserts that every subtable is a cursive-attachment subtable (LookupType 3), that all of a lookup's anchors share one Y value that is a whole pixel (font units ÷ 50), and that no two lookups share a height. The font has four such lookups, at font-unit Y 0/250/300/400, which is pixel y 0/5/6/8; `test_classifier_heights` and `test_classifier_discovers_four_curs_lookups` check this.
2. An adjacent output-glyph pair (left, right) in different clusters is joined at height h when the left glyph has an ExitAnchor and the right glyph has an EntryAnchor in the height-h lookup. It is a `break` when no lookup pairs them. This matches the test suite's anchor-Y intersection (`_compiled_glyph_meta` in `test/test_shaping.py`). Because each height has its own lookup, a join cannot connect two different heights.
3. A pair in the same cluster is not an output seam; the input seam is `lig`.

Ink-gap arithmetic (`test/test_join_ink.py`) is not a baseline column. It detects defects, which is the job of the `E-UNREALIZED` check in `rebuild/pipeline/defects.py` (`doc/rebuild-design.md` §9), and it records no outcome.

The baseline has no split-buffer cross-check. For the M1 font, gate:conform's split-buffer check (`conform.check_split_buffer`) compares every text that contains a space or ZWNJ with its segments shaped separately.

## 5. Configurations

The configurations are the tokens in `rebuild/validation/rowmodel.CONFIGS`; the extractor's copy is `rebuild/baseline/model.CONFIGS`. They include every stylistic set the font has, each on its own (ss02–ss07 and ss10; the font has no ss01, ss08, or ss09). Every Manual pin names a single set, so the single sets cover every configuration the Manual's pins use. The M1 acceptance configurations (`conform.ACCEPTANCE_CONFIGS`) use a subset of these tables.

| #  | Config token     | Feature dict     | Why                                                                                                                                  |
| -- | ---------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| 1  | `default`        | (empty)          | The font as shipped; the primary oracle.                                                                                             |
| 2  | `ss02`           | ss02             | Single set; the Manual pins it on `·May ~b~ ·I ~x~ ·Tea.!half` and two similar runs.                                                 |
| 3  | `ss03`           | ss03             | Single set; the Manual pins `·At \| ·Fee ~x~ ·Tea ~b~ ·Utter ~x~ ·Roe`.                                                              |
| 4  | `ss04`           | ss04             | Single set; the Manual pins `·He ~b~ ·At ~x~ ·No ~x~ ·Day ~b~ ·It ~b~ ·Utter ~x~ ·Roe`.                                              |
| 5  | `ss05`           | ss05             | Single set; the Manual pins `·Bay \| ·Et ~b~ ·Tea ~b~ ·Utter ~x~ ·Roe`.                                                              |
| 6  | `ss06`           | ss06             | Single set; gapped ·Owe. The Manual's ss06 div is CSS-visual only and has no shaped pin, so the baseline is its first shaped record. |
| 7  | `ss07`           | ss07             | Single set; the Manual pins `·At ~x~ ·No ~x~ ·Owe ~x~ ·Day`.                                                                         |
| 8  | `ss10`           | ss10             | Single set; the Manual pins it on an inner span of the `·At ~x~ ·No \| ·Tea ~x~ ·It …` run.                                          |
| 9  | `ss02+ss03`      | ss02, ss03       | Multi-set combination declared by this plan: both sets gate qsTea entry stances, so they may interact.                               |
| 10 | `ss06+ss07`      | ss06, ss07       | Multi-set combination declared by this plan: both sets reshape qsOwe.                                                                |
| 11 | `ss02+ss03+ss05` | ss02, ss03, ss05 | Multi-set combination declared by this plan and the §7 conformance-matrix example: all three sets touch qsTea's capability matrix.   |
| 12 | `ss03+ss05`      | ss03, ss05       | The multi-set combination in `conform.SETTLEMENT_CONFIGS`, extracted so the M1 oracle has an old-font baseline for it.               |

The corpus declares no multi-set combination.

## 6. The equivalence triage

§3.4 defines `word: initial` as a left context that is an edge, a space, or a ZWNJ. Post-ZWNJ and post-space positions are therefore word-initial by definition, and positions before a boundary are word-final. The old font maintains these equivalences by hand and incompletely: it fires `.noentry` rules against literal `uni200C`, and `qsExcite` guards on space, ZWNJ, and the namer dot.

The equivalence triage is a one-time comparison of each basis string `w` with the same string beside a boundary. Its code is not in the tree. Its outcome is the `boundary-echo` class in `rebuild/m1-divergences.yaml`, whose `why:` states the boundary-equals-word-boundary rule. On every build, gate:conform's split-buffer check (`conform.check_split_buffer`) checks the same equivalence over the M1 font. `rebuild/M1-PLAN.md` refers to the four checks by name:

| Check           | Eligible `w`                | Comparison                                                                                           |
| --------------- | --------------------------- | ---------------------------------------------------------------------------------------------------- |
| `zwnj-vs-edge`  | first symbol not space/ZWNJ | shape `ZWNJ + w` in one buffer; compare the `w` portion (clusters ≥ 1) with the baseline row for `w` |
| `space-vs-edge` | first symbol not space/ZWNJ | shape `space + w`; same comparison                                                                   |
| `edge-vs-zwnj`  | last symbol not space/ZWNJ  | shape `w + ZWNJ`; compare the `w` portion with the baseline row for `w`                              |
| `edge-vs-space` | last symbol not space/ZWNJ  | shape `w + space`; same comparison                                                                   |

The triage classifies a divergence in the `w` portion as `glyph`, `seam`, or `position-only` (positions differ while glyph names, clusters, and seams agree). The namer dot does not split runs (§3.4), so no namer-dot check exists; namer-dot contexts are ordinary basis rows.

## 7. Validation

The extraction outputs are trusted only when both layers below pass.

### Corpus pin replay

`rebuild/validation/pins.py` collects every data-expect run from the three corpora (`site/index.html`, `site/the-manual.html`, `site/extra-senior-words.html`). It uses the test suite's collector and parser, imported read-only from `test/test_shaping.py`: `_DataExpectCollector`, `parse_expect`, and the helpers `_partition_by_runs`, `_token_char_spans`, and `_expand_maybe_ligatures`. It replays each Senior run whose text is inside the basis alphabet and whose configuration is one of the §5 tokens. It counts the runs it skips: Junior runs, runs with other symbols, and runs in other configurations.

- Each run is shaped with the validation suite's own shaper and classifier (`validation/shaping.py`, `validation/classify.py`) under the configuration its `data-stylistic-set` gives. The replay checks base glyph names and the `half`/`alt` traits, join heights (`~b~`/`~x~`/`~6~`/`~t~` = y0/y5/y6/y8), bare joins (any height), `|` and `|?|` breaks (the seam must be `break`), and `+`/`+?`/`+|` ligatures as the parser expands them. It skips and counts other variant assertions. The existing suite checks these pins against the same font, so a disagreement is treated as a validation-suite bug until shown otherwise.
- The tables need no row-by-row replay, because a row is a pure function of the font bytes, the alphabet, and the extractor code. On every `ensure_fresh`, `baseline_subset.prove_font_provenance` compares each header's `font_sha256` with the font that header names, accepting a font that differs only in its `head` and `name` tables. `run_m1` therefore fails rather than compare against tables that another font produced. The header's `alphabet_sha256` identifies the alphabet, and the determinism and header tests in `rebuild/test_extractor.py` cover the extractor code.

The replay is `rebuild/test_validation_suite.py::test_full_corpus_replay_live`. It runs in `make test-rebuild`'s contracts lane and fails it on any disagreement.

### Extractor unit tests

`rebuild/test_extractor.py`, `rebuild/test_validation_suite.py`, and `rebuild/test_baseline_subset.py`, run with `uv run pytest rebuild/ -n auto --dist worksteal`. They cover:

- GPOS discovery: `curs` references four cursive lookups, at pixel heights {0, 5, 6, 8}.
- Name recovery: the font's glyph names longer than 63 bytes resolve correctly through `TTFont.getGlyphName`, and `glyph_to_string` truncates them, which is why the shaper does not use it.
- Cluster alignment: ·Day·Utter ligates into one glyph covering two input positions with a `lig` seam, and ·May·Tea does not ligate.
- Classifier checks against corpus-pinned facts: a y5 join, a y0 join, a break, and a join that appears only under its stylistic set.
- Determinism: one subset extracted at two workers and at one gives identical bytes; row order matches §3; header content is complete.
- Split shaping: `Shaper.shape_split` reports clusters in whole-text coordinates.
- Sampling: the `--sample` predicate selects the same strings on every run.
- The subset filter, its freshness stamp, and the font-provenance check (`rebuild/test_baseline_subset.py`).

No test pins an outcome that the corpus does not already establish. The baseline records current behavior; it does not assert what the behavior should be.

## 8. Module and file layout

The extractor (`rebuild/baseline/`) and the validation suite (`rebuild/validation/`) are separate implementations. The extractor has the alphabet, shaper, classifier, extraction orchestration, and CLI. The validation suite has its own row model, shaper, and classifier, the §7 pin replay, and the table readers the M1 pipeline uses. Both implement the §3 row format, so the TSV format is the interface between them, not any one Python class.

```text
rebuild/
  BASELINE-PLAN.md                 this document
  baseline/
    __init__.py
    model.py                       Row dataclass, config tokens, row ordering, TSV serialization + parsing, header rendering
    alphabet.py                    the 47-symbol alphabet (codepoints + names), basis enumeration, shard partitioning by (length, first symbol)
    shaper.py                      per-process state: hb.Font + TTFont + one reused hb.Buffer; shape(text, features) -> ShapeResult; split-buffer shaping
    classify.py                    GPOS curs-lookup discovery, per-height entry/exit sets, classify(left, right) -> token
    extract.py                     orchestration: multiprocessing pool, shard workers, deterministic merge, gzip writing, digest/summary generation
    cli.py                         argparse front end
  validation/
    __init__.py
    rowmodel.py                    the §3 row format as the validation suite implements it, plus table reading and chunking
    shaping.py                     hb.Font + TTFont name recovery, one reused buffer, per-row seam extraction
    classify.py                    the §4 black-box seam classifier
    pins.py                        read-only import of the test/ collector + parser; the §7 replay against library shaping
  check_determinism.py             the §2 determinism check: extraction output is byte-identical across two runs
  test_extractor.py                extractor unit tests
  test_validation_suite.py         the §7 validation-suite tests
  test_baseline_subset.py          baseline_subset filter tests
  out/                             generated, gitignored: baseline-<config>.tsv.gz, digest-<config>.json, digests.tsv, SUMMARY.md
```

Public interfaces:

- `model.Row`: a frozen dataclass with `codepoints: tuple[int, ...]`, `glyphs: tuple[str, ...]`, `clusters: tuple[int, ...]`, `seams: tuple[str, ...]`, and `positions: tuple[tuple[int, int, int], ...]`; `Row.to_tsv() -> str`, `Row.from_tsv(line) -> Row`, `row_sort_key(row)`; `CONFIGS: dict[str, dict[str, bool]]` (the §5 list, in order).
- `shaper.Shaper`: `Shaper(font_path)`, `shape(text: str, features: dict[str, bool]) -> ShapeResult` (names via TTFont, clusters, positions).
- `classify.SeamClassifier`: `SeamClassifier(font_path)`, `heights() -> tuple[int, ...]`, `classify(left_glyph: str, right_glyph: str) -> str`.
- `extract.extract_config(config_token: str, out_dir: Path, workers: int = SHARD_WORKERS_DEFAULT) -> Digest` and `extract.run_all(out_dir, workers)`.
- `validation.pins` exposes `collect_pin_runs`, `check_pin`, and `ReplayReport`.
- The M1 pipeline reads the tables through `validation.rowmodel` (`open_table`, `read_header`, `iter_rows`).

CLI (all via `uv run`):

```sh
uv run python -m rebuild.baseline.cli extract --config default --out rebuild/out
uv run python -m rebuild.baseline.cli extract --all --out rebuild/out
uv run python -m rebuild.baseline.cli summarize --out rebuild/out
```

The pin replay is a test, and the determinism check is a script:

```sh
uv run pytest rebuild/test_validation_suite.py -n auto --dist worksteal
uv run python rebuild/check_determinism.py --config default --lengths 1,2
```

Neither block passes `--workers`. The extractor defaults to `SHARD_WORKERS_DEFAULT` (§2). `check_determinism.py` has no `--workers` option. Its default mode shapes the length-1 and length-2 basis twice, each time in one fresh subprocess with a different `PYTHONHASHSEED`, and compares the output. Its `--command` mode runs the given extractor command twice, at the same width, and compares the named artifact. Pass `--workers` to the extractor only to leave cores free for other work, or pass `--workers 1` to extract in-process for a readable traceback.
