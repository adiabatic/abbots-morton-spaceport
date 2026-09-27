# Baseline tables

This package is the baseline extractor that `doc/rebuild-design.md` §13 (item 1) calls for. It shapes every input string up to length 4 through the built Senior Sans font (`site/AbbotsMortonSpaceportSansSenior-Regular.otf`), treating the font as a black box, and records each string's outcome as a diff-stable table under `rebuild/out/`. The M1 oracle and the review corpus read these tables as the old font's behavior. A bare section number here (§3.4, §6.1, §10) is a section of `doc/rebuild-design.md`.

The validation suite in `rebuild/validation/` is a separate implementation with its own row model, shaper, and junction classifier, plus the corpus-pin replay and the table readers the M1 pipeline uses (`rowmodel.open_table`, `read_header`, and `iter_rows`). Both implement the table format below, so the TSV format is the interface between them, not any one Python class.

Each table's header records its provenance, and `baseline_subset.check_font_provenance` checks the header's font against the font on disk on every `ensure_fresh`. The headers record the values below, except that the `ss03+ss05` table, extracted later, records repo SHA `0cd71c4`:

| Fact         | Value                                                              |
| ------------ | ------------------------------------------------------------------ |
| Repo SHA     | `ae9d08d`                                                          |
| Font         | `site/AbbotsMortonSpaceportSansSenior-Regular.otf`                 |
| Font SHA-256 | `3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35` |

## The basis

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

Across the configurations: **4,985,760 × |`rowmodel.CONFIGS`| rows**.

### Why this realizes the §6.1 windows

A settlement window is the resolved left neighbor, the letter itself, and up to four raw letters to its right (§10). Black-box extraction cannot set the resolved left state directly, so the string prefix induces it. In a length-4 string, the letter at position 2 has a resolved left induced by a one-symbol prefix (every state reachable in one step from a run edge) and two raw-right symbols. Position 1 of every string gives every run-initial window (resolved left = edge) with up to three raw-right symbols. Strings with a space or ZWNJ inside give word-final and word-initial windows mid-string: `qsMay space qsTea qsKey` gives word-final ·May and word-initial ·Tea with lookahead in one row. Every position of every string is recorded, aligned by cluster, so later positions also contribute windows, with fewer raw-right symbols.

### What strings up to length 4 cannot capture, accepted by design

- **Resolved left states that need two or more settled joins, with full lookahead.** Such a state first appears at position 3, where a length-4 string leaves at most one raw-right symbol. The basis supplies a third raw-right symbol only at position 1 and never a fourth. The baseline makes no completeness claim for these windows. For M1, the witness stage settles a certificate text of any length for every emitted rule, and the deep sweep and the deep replay check texts of length 5 and more (§10).
- **Longer-range emergent effects**, such as the depth-5-only regressions the archive documents (§15, item 11). The deep sweep (`make conform-deep`) checks depth 5 and beyond.
- **No window-keyed deduplication.** Keying rows by window instead of by string would assume the locality that this baseline exists to measure: that context beyond the window does not matter, and that boundary tokens behave like run edges. Full enumeration is cheap enough that deduplication is not needed.

## Table format

One file per configuration: `rebuild/out/baseline-<config>.tsv.gz`, where `<config>` is one of the tokens in `rebuild/validation/rowmodel.CONFIGS`. One row per input string, with no deduplication. Tab-separated, UTF-8, `\n` line endings.

`model.render_header` writes these header lines, each beginning with `#` and a space, in this order:

```text
# baseline-extract v<tool version>
# git_sha: <short repo SHA at extraction>
# font: site/AbbotsMortonSpaceportSansSenior-Regular.otf
# font_sha256: <SHA-256 of the font>
# config: <config token> (<enabled features, e.g. ss02=1 ss03=1; empty for default>)
# subset: <limit=N, or sample=N modulus=M; smoke runs only>
# alphabet_sha256: <SHA-256 of the newline-joined sorted codepoint list>
# columns: codepoints glyphs clusters junctions positions
```

The `subset` line appears only in a smoke run (`--limit` or `--sample`), so a partial table cannot be mistaken for a full one.

Columns:

| Column       | Content                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `codepoints` | The input string as colon-joined uppercase hex codepoints, e.g. `E665:0020:E652:00B7`. The symbol legend is in `SUMMARY.md`.                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `glyphs`     | Resolved **full** glyph names in output order, pipe-joined, e.g. `qsMay.en-y0.ex-y5\|qsTea.half`. Names come from `TTFont.getGlyphName(gid)`, because HarfBuzz's `glyph_to_string` truncates names to 63 bytes.                                                                                                                                                                                                                                                                                                                                                                |
| `clusters`   | Comma-joined cluster index per output glyph: the earliest input position the glyph covers. A ligature is one glyph covering two input positions. Derive per-position names from `glyphs` and `clusters`, not from index arithmetic.                                                                                                                                                                                                                                                                                                                                            |
| `junctions`  | One token per input junction (positions k and k+1, for k = 0 … len−2), comma-joined. The token is `lig` when no output glyph starts at position k+1, because a ligature consumed the junction. Otherwise it is the classification of the flanking output glyphs (see Junction classification): `y0`, `y5`, `y6`, or `y8` for a join at that pixel height, or `break` for no join. If more than one height matched, the heights are `+`-joined in ascending order (e.g. `y0+y5`), and the extraction raises an assertion after writing the table. No such junction is expected. |
| `positions`  | `x_offset,y_offset,x_advance` per output glyph in font units, pipe-joined, e.g. `0,0,350\|0,250,250`. This records cursive-attachment offsets and advances, so a later comparison can detect extension changes (which also appear as `ex-ext-N` name changes), kerning changes, and attachment shifts. `y_advance` is omitted, and the shaper raises an assertion if it is ever nonzero.                                                                                                                                                                                       |

Row order: by string length, then by codepoint tuple, both ascending (`model.row_sort_key`). Every value is an integer or a name; there is no floating point. The same font, tool version, and commit give byte-identical uncompressed output.

## Junction classification

`JunctionClassifier` reads join heights from the built font's GPOS:

1. At start-up it collects the lookups that the `curs` feature references, so no lookup index is hardcoded. For each lookup it records the glyphs with an ExitAnchor and the glyphs with an EntryAnchor. It asserts that every subtable is a cursive-attachment subtable (LookupType 3), that all of a lookup's anchors share one Y value that is a whole pixel (font units ÷ 50), and that no two lookups share a height. The font has four such lookups, at font-unit Y 0/250/300/400, which is pixel y 0/5/6/8; `test_classifier_heights` and `test_classifier_discovers_four_curs_lookups` check this.
2. An adjacent output-glyph pair (left, right) in different clusters is joined at height h when the left glyph has an ExitAnchor and the right glyph has an EntryAnchor in the height-h lookup. It is a `break` when no lookup pairs them. This matches the test suite's anchor-Y intersection (`_compiled_glyph_meta` in `test/test_shaping.py`). Because each height has its own lookup, a join cannot connect two different heights.
3. A pair in the same cluster is not an output junction; the input junction is `lig`.

Ink-gap arithmetic (`test/test_join_ink.py`) is not a baseline column. It detects defects, which is the job of the `E-UNREALIZED` check in `rebuild/pipeline/defects.py` (§9), and it records no outcome.

The baseline has no split-buffer cross-check. For the M1 font, gate:conform's split-buffer check (`conform.check_split_buffer`) compares every text that contains a space or ZWNJ with its segments shaped separately.

## Configurations

`rebuild/validation/rowmodel.CONFIGS` is the authority on the configurations and their order; the extractor's copy is `rebuild/baseline/model.CONFIGS`. They include every stylistic set the font has, each on its own (ss02–ss07 and ss10; the font has no ss01, ss08, or ss09). Every Manual pin names a single set, so the single sets cover every configuration the Manual's pins use. The corpus declares no multi-set combination; the multi-set ones are the baseline's own. The M1 acceptance configurations (`conform.ACCEPTANCE_CONFIGS`) use a subset of these tables.

| Config token     | Why                                                                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `default`        | The font as shipped; the primary oracle.                                                                                             |
| `ss02`           | Single set; the Manual pins it on `·May ~b~ ·I ~x~ ·Tea.!half` and two similar runs.                                                 |
| `ss03`           | Single set; the Manual pins `·At \| ·Fee ~x~ ·Tea ~b~ ·Utter ~x~ ·Roe`.                                                              |
| `ss04`           | Single set; the Manual pins `·He ~b~ ·At ~x~ ·No ~x~ ·Day ~b~ ·It ~b~ ·Utter ~x~ ·Roe`.                                              |
| `ss05`           | Single set; the Manual pins `·Bay \| ·Et ~b~ ·Tea ~b~ ·Utter ~x~ ·Roe`.                                                              |
| `ss06`           | Single set; gapped ·Owe. The Manual's ss06 div is CSS-visual only and has no shaped pin, so the baseline is its first shaped record. |
| `ss07`           | Single set; the Manual pins `·At ~x~ ·No ~x~ ·Owe ~x~ ·Day`.                                                                         |
| `ss10`           | Single set; the Manual pins it on an inner span of the `·At ~x~ ·No \| ·Tea ~x~ ·It …` run.                                          |
| `ss02+ss03`      | Multi-set: both sets gate qsTea entry stances, so they may interact.                                                                 |
| `ss06+ss07`      | Multi-set: both sets reshape qsOwe.                                                                                                  |
| `ss02+ss03+ss05` | Multi-set: all three sets touch qsTea's capability matrix (§5.5).                                                                    |
| `ss03+ss05`      | The multi-set combination in `conform.SETTLEMENT_CONFIGS`, extracted so the M1 oracle has an old-font baseline for it.               |

ss06 and ss07 reshape only ·Owe, so while ·Owe is outside `baseline_subset.M1_ALPHABET`, the `ss06`, `ss07`, and `ss06+ss07` subset tables are row-identical to `default`'s. The acceptance gate covers them by running `default` alone (`baseline_subset.DEFAULT_COVERED_CONFIGS`), and every refilter checks that identity. The ·Owe migration makes them diverge, and each then goes into `conform.ACCEPTANCE_CONFIGS` and out of `DEFAULT_COVERED_CONFIGS`, as `SubsetIdentityError`'s message says.

## Runtime, determinism, and outputs

- **Parallelism.** Work is sharded by (length, first symbol), 47 shards per length. `extract.SHARD_WORKERS_DEFAULT` is the default width, and its docstring says why it is a core count and not a memory budget. At more than one worker, each worker is a `spawn` process that builds its own `Shaper` and `JunctionClassifier`; the shaper reuses one `hb.Buffer` and copies `glyph_infos` and `glyph_positions` out before the next shape. Pass `--workers` to the extractor only to leave cores free for other work, or pass `--workers 1` to extract in-process for a readable traceback.
- **Determinism under parallelism.** Each worker writes its shard to a temporary file, and the writer concatenates the shards in shard-key order. Within a shard, rows are generated in the canonical row order. The output bytes therefore do not depend on scheduling or on the worker count; `rebuild/test_extractor.py::test_small_extraction_is_deterministic` extracts at two workers and at one and compares the bytes. Two extractions at the same commit on the same font produce byte-identical uncompressed streams. The header records the commit, so extractions at different commits differ in that line. `rebuild/check_determinism.py` compares two runs byte for byte, and its docstring gives its two modes.
- **Sizes.** A configuration's uncompressed table is hundreds of megabytes, so every table is written gzipped (`.tsv.gz`, with `mtime=0` so the gzip bytes are deterministic too). SHA-256 digests are computed over the uncompressed stream, header included.
- **`rebuild/out/` is gitignored.** Besides the tables, the extractor writes small, regenerable summaries that a later re-extraction can be compared against without the bulk files. `digest-<config>.json` holds a configuration's row count, the SHA-256 of its uncompressed stream, its junction counts per `y0/y5/y6/y8/lig/break`, and its full resolved-glyph-name frequency table. `digests.tsv` holds the row count, SHA-256, junction counts, and subset of every configuration that has a digest file. The `summarize` subcommand writes `SUMMARY.md`: the provenance, the symbol legend, the digest table, and each configuration's most frequent glyph names with its distinct-name count.

## Validation

The extraction outputs are trusted only when both layers below pass.

### Corpus pin replay

`rebuild/validation/pins.py` replays every Senior data-expect run from the three corpora whose text is inside the basis alphabet and whose configuration is in `CONFIGS`, shaping each with the validation suite's own shaper and classifier under the configuration its `data-stylistic-set` gives, and counts the runs it skips; its docstring gives the rules. The replay checks base glyph names and the `half`/`alt` traits, join heights (`~b~`/`~x~`/`~6~`/`~t~` = y0/y5/y6/y8), bare joins (any height), `|` and `|?|` breaks (the junction must be `break`), and `+`/`+?`/`+|` ligatures as the parser expands them. It skips and counts other variant assertions.

The tables need no row-by-row replay, because a row is a pure function of the font bytes, the alphabet, and the extractor code. `baseline_subset.check_font_provenance` ties each header's `font_sha256` to the font on disk, the header's `alphabet_sha256` identifies the alphabet, and the determinism and header tests in `rebuild/test_extractor.py` cover the extractor code.

The replay is `rebuild/test_validation_suite.py::test_full_corpus_replay_live`. It runs in `make test-rebuild`'s contracts lane and fails it on any disagreement.

### Unit tests

`rebuild/test_extractor.py`, `rebuild/test_validation_suite.py`, and `rebuild/test_baseline_subset.py` cover:

- GPOS discovery: `curs` references four cursive lookups, at pixel heights {0, 5, 6, 8}.
- Name recovery: the font's glyph names longer than 63 bytes resolve correctly through `TTFont.getGlyphName`, and `glyph_to_string` truncates them, which is why the shaper does not use it.
- Cluster alignment: ·Day·Utter ligates into one glyph covering two input positions with a `lig` junction, and ·May·Tea does not ligate.
- Classifier checks against corpus-pinned facts: a y5 join, a y0 join, a break, and a join that appears only under its stylistic set.
- Determinism: one subset extracted at two workers and at one gives identical bytes; row order matches the table format; header content is complete.
- Split shaping: `Shaper.shape_split` reports clusters in whole-text coordinates.
- Sampling: the `--sample` predicate selects the same strings on every run.
- The subset filter, its freshness stamp, and the font-provenance check (`rebuild/test_baseline_subset.py`).

No test pins an outcome that the corpus does not already establish. The baseline records current behavior; it does not assert what the behavior should be.

## Layout and commands

The extractor (`rebuild/baseline/`) has the alphabet, shaper, classifier, row model and header rendering, extraction orchestration, and CLI. The validation suite (`rebuild/validation/`) has its own row model, shaper, and classifier, the pin replay, and the table readers the M1 pipeline uses. Each module's docstring states its part.

```sh
uv run python -m rebuild.baseline.cli extract --config default --out rebuild/out
uv run python -m rebuild.baseline.cli extract --all --out rebuild/out
uv run python -m rebuild.baseline.cli summarize --out rebuild/out
uv run pytest rebuild/test_extractor.py rebuild/test_validation_suite.py rebuild/test_baseline_subset.py -n auto --dist worksteal
uv run python rebuild/check_determinism.py --config default --lengths 1,2
```
