# Baseline extraction plan (§13.1)

This plan governs Phase 3 (implementation) and Phase 4 (extraction runs) of the §13.1 baseline: shape the depth-2 basis through the current built Senior Sans font (`site/AbbotsMortonSpaceportSansSenior-Regular.otf`), black-box, and record every window’s resolved outcome as a diff-stable table under `rebuild/out/`. The table is the migration oracle and the review surface’s first real workload. Grounding documents: `doc/rebuild-design.md` §3.4, §6.1, §8, §10, §13.1, and `rebuild/recon/baseline-recon.md` (all file:line references below that are not given explicitly are in the recon).

Fixed provenance for every run under this plan:

| Fact         | Value                                                              |
| ------------ | ------------------------------------------------------------------ |
| Repo SHA     | `ae9d08d`                                                          |
| Font         | `site/AbbotsMortonSpaceportSansSenior-Regular.otf`                 |
| Font SHA-256 | `3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35` |
| Machine      | Apple Silicon, 12 logical cores (8 performance)                    |

## 1. The basis

### Alphabet

47 symbols, exactly the recon’s census:

- The 44 Quikscript runes, U+E650–U+E66C and U+E670–U+E67E (`doc/glyph-names.md`; the angle parens U+E66E/E66F are punctuation and excluded).
- `space` (U+0020) and ZWNJ (U+200C), the run-splitting boundary tokens.
- The namer dot, `periodcentered` (U+00B7). It is load-bearing both as a conditioned-on symbol (`qsExcite.exit_baseline_before_vertical` guards on it where ·Utter does not — the exact §3.4 distinction) and as a conditioning-dependent symbol (its own `periodcentered.lowered` form depends on the following rune’s height class and on word position). Per §3.4 it is a registered boundary token that does not split runs, so it belongs in the alphabet proper, not just at run edges.

### Input set

All strings of length 1 through 4 over the 47-symbol alphabet, every string shaped in its own fresh HarfBuzz buffer (so string start and end are genuine run edges — word edges are implicit, not authored).

Row-count arithmetic, per configuration:

| Length | Count           |
| ------ | --------------- |
| 1      | 47              |
| 2      | 47² = 2,209     |
| 3      | 47³ = 103,823   |
| 4      | 47⁴ = 4,879,681 |
| Total  | **4,985,760**   |

Across the 11 configurations of §5: **54,843,360 rows**.

### Why this realizes the §6.1 windows

The settlement model’s window is `[resolved-left, self, raw-right, raw-right²]`. Black-box, resolved-left state cannot be injected — it is induced by the string prefix. Length-4 strings give every `self` at position 2 a full window: resolved-left induced by a length-1 prefix (every state reachable in one step from a run edge), plus both raw-right symbols. Positions 1 of every string realize every run-initial window (`resolved-left = edge`) with full raw-right context. Strings containing space/ZWNJ in interior positions realize the word-final and word-initial windows mid-string (e.g. `qsMay space qsTea qsKey` realizes word-final ·May and word-initial ·Tea-with-lookahead in one row). Every position of every string is recorded (cluster-aligned), so deeper positions contribute windows too, with correspondingly truncated raw-right context.

### What depth-2 cannot capture, accepted by design

- **Deeper-left-induced resolved states with full lookahead.** A resolved-left state that only arises after two or more settled joins (position ≥ 3) appears in this basis only with 0–1 symbols of raw-right context; giving it both lookahead symbols would need length-5+ strings. §10’s per-transition conformance gate is the designed closure: it derives a shortest example sequence per decision-table transition, including sequences longer than 5 runes that no fixed-depth sweep can reach. That gate, not this baseline, owns completeness.
- **Longer-range emergent effects** (the archive’s documented depth-5-only regressions, §15.11). Same disposition.
- **Window-keyed dedup is rejected.** A “smarter” basis that keys rows by window rather than by string would have to assume the very locality (context-freeness beyond the window, boundary-token equivalence to run edges) that this baseline exists to measure; the §6 equivalence triage and §4 split-buffer cross-check are checks on those assumptions, so the input set must not presuppose them. Full enumeration is cheap enough (§2) that no dedup is warranted.

## 2. Runtime and size strategy

Recon-measured throughput on this machine: ≈91,500 shapes/s single-threaded with a stylistic-set feature dict, full-name recovery, and position extraction (≈10.9 µs/shape).

- Shaping cost per configuration: 4,985,760 shapes ≈ **55 s single-threaded**. With classification, serialization, and gzip the honest end-to-end estimate is 3–4× that, ≈ **2.5–3.5 min single-threaded per configuration** — already inside the ≤20 min budget with an order of magnitude to spare.
- **Multiprocessing anyway**: `multiprocessing` with **10 worker processes** (12 logical cores, two left for the writer and the OS), mirroring the pytest-xdist subprocess pattern — each worker builds its own `hb.Font`, `TTFont`, and one reused `hb.Buffer`, materializing `glyph_infos`/`glyph_positions` before the next shape. Work is sharded by first symbol (47 shards per configuration, matching the `test_join_ink.py` parametrization pattern). Expected wall clock: **≈20–30 s per configuration**, ≈5–6 min for all 11 configurations, plus ≈4–6 min for the equivalence pass (§6, four extra shapes per eligible string). Whole Phase 4 extraction: **well under 15 minutes total**.
- **Determinism under parallelism**: workers write per-shard temporary files; the writer concatenates shards in shard-index order, and rows within a shard are generated in the canonical row order (§3), so output bytes are independent of scheduling. Two runs on the same font must produce byte-identical uncompressed streams; the digest file makes that checkable.
- **Sizes**: ≈120–160 bytes/row raw → ≈0.6–0.8 GB per configuration uncompressed, comfortably over the ~50 MB threshold, so every bulk table is written gzipped (`.tsv.gz`, `mtime=0` so gzip output is also deterministic): ≈40–70 MB per configuration, ≈0.5–0.8 GB total under `rebuild/out/`. The gzip members are for storage only; SHA-256 digests are computed over the uncompressed stream.
- **`rebuild/out/` is gitignored** (the implementation step adds the `rebuild/out/` line to `.gitignore` — recon confirmed it is absent today). The committed-shape artifact is the digest summary: `rebuild/out/SUMMARY.md` plus `rebuild/out/digests.tsv` — per configuration: row count, SHA-256 of the uncompressed stream, seam-classification histogram (counts per `y0/y5/y6/y8/lig/break`), resolved-glyph-name frequency table (top section plus total distinct), and equivalence-divergence counts. The summary is small (a few KB), regenerable, and is the piece `rebuild/BASELINE-REPORT.md` quotes verbatim — so a future re-extraction diffs meaningfully against the report without the bulk files.

## 3. The table schema

One file per configuration: `rebuild/out/baseline-<config>.tsv.gz` where `<config>` is `default`, `ss02` … `ss10`, `ss02+ss03`, `ss06+ss07`, `ss02+ss03+ss05`. One row per input string (no dedup, §1). Tab-separated, UTF-8, `\n` line endings.

Header lines (each beginning with `#` and a space), in fixed order:

```text
# baseline-extract v<tool version>
# git_sha: ae9d08d
# font: site/AbbotsMortonSpaceportSansSenior-Regular.otf
# font_sha256: 3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35
# config: <config token>   (feature dict, e.g. ss02=1 ss03=1; "default" = empty dict)
# alphabet_sha256: <sha256 of the newline-joined sorted codepoint list>
# columns: codepoints glyphs clusters seams positions
```

Columns:

| Column       | Content                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `codepoints` | Input string as colon-joined uppercase hex codepoints, e.g. `E665:0020:E652:00B7`. The human-readable symbol mapping lives once in `SUMMARY.md`, not per row.                                                                                                                                                                                                                                                                                                                                                               |
| `glyphs`     | Resolved **full** glyph names in output order, pipe-joined. Names come from `TTFont.getGlyphName(gid)`, never `font.glyph_to_string` (the 63-byte truncation). E.g. `qsMay.en-y0.ex-y5\|qsTea.half`.                                                                                                                                                                                                                                                                                                                        |
| `clusters`   | Comma-joined cluster index per output glyph (the earliest input position each glyph covers). This is the per-position alignment: a ligature shows as one glyph covering two input positions; per-position resolved names are derived from `glyphs` + `clusters`, never from index math.                                                                                                                                                                                                                                     |
| `seams`      | One token per input seam (input positions k, k+1 for k = 0 … len−2), comma-joined: `lig` if both positions fall in the same cluster (the seam was consumed by ligation); otherwise the §4 classification of the flanking output-glyph pair — `y0`, `y5`, `y6`, `y8` for joined at that pixel height, `break` for no join. If the height intersection ever has more than one element, all heights are emitted sorted and `+`-joined (e.g. `y0+y5`) and the run fails a sanity assertion for investigation; none is expected. |
| `positions`  | Per output glyph `x_offset,y_offset,x_advance` (font units), pipe-joined, e.g. `0,0,350\|0,250,250`. This captures cursive-attachment offsets and advances, sufficient to later detect extension-amount changes (which also surface as `ex-ext-N` name changes), kern changes, and attachment drift, without committing to any interpretation of them. `y_advance` is omitted (always 0 for this font; an extractor assertion enforces that).                                                                               |

Row order: by string length ascending, then by the codepoint tuple ascending — deterministic, definable without reference to any separator’s collation. No floating-point anywhere; every value is an integer or a name. Diff stability per §8: same font + same tool version ⇒ byte-identical uncompressed output.

## 4. Seam classification

The classifier is the recon item-1 procedure, anchor-Y intersection read black-box from the built font’s GPOS:

1. At extractor start-up, walk the font’s GPOS `curs` feature to its cursive-attachment (LookupType 3) lookups — discovered by feature reference, never hardcoded lookup indices. Recon verified there are exactly four, one per join height, with anchor Y at 0/250/300/400 font units = pixel y 0/5/6/8 (font units ÷ 50). For each lookup, record the per-height sets {glyphs with an ExitAnchor} and {glyphs with an EntryAnchor}; assert each lookup’s anchors are uniform in Y-per-height semantics (every non-NULL anchor in the y-h lookup sits at h × 50).
2. For an adjacent output-glyph pair (left, right) not in the same cluster: joined at height h iff the left glyph has an ExitAnchor and the right glyph has an EntryAnchor in the same height-h lookup; `break` iff no lookup pairs them. This is exactly equivalent to the test suite’s anchor-Y intersection (`test/test_shaping.py:530-579`, `_pair_join_ys`); per-height lookups are why cross-height attachment is structurally impossible.
3. Same-cluster pairs do not exist as output seams; the input seam is `lig`.

Ink-gap arithmetic (`test/test_join_ink.py`) is deliberately **not** part of the baseline columns — it is a defect detector that belongs to the §9 `E-UNREALIZED` gate, not an outcome fact.

**Split-buffer cross-check policy** (the `_isolation_glyphs_split` technique, `test/test_shaping.py:710-735`): the cross-check validates that a `break` classification corresponds to genuinely uncoupled shaping, but a known, accepted class of isolation leaks exists today (the corpus distinguishes `|` from `|?|` for exactly this reason), so full-basis coverage would mostly re-measure known behavior at ~2× cost. Policy:

- **All length-2 strings** (2,209 per configuration — every pair seam in run-isolated context), every configuration.
- **A deterministic 1% sample of length-3/4 strings containing at least one `break` seam**, selected by a fixed-seed hash of the codepoint tuple (so the sample is identical across runs and configurations), every configuration.

For each sampled break seam, shape the string split at the seam and compare the flanking glyphs (names and positions) against full-buffer shaping. Disagreements go to `rebuild/out/split-check-disagreements.tsv` (config, codepoints, seam index, full-buffer glyphs, split glyphs) as triage rows — they are current-font facts (isolation leaks), not extraction failures; the baseline records the full-buffer truth. A disagreement rate materially above the known leak census is the signal to widen the sample.

## 5. Configurations

Eleven, per §10 tier 3 (“each set alone, every configuration the Manual’s pins use, and at least one declared multi-set combination”) and the recon’s feature census (ss02–ss07, ss10 exist; no ss01/ss08/ss09; every Manual pin is a single set, so the singles superset the Manual’s configurations):

| #   | Config token     | Feature dict     | Why                                                                                                                                    |
| --- | ---------------- | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `default`        | (empty)          | The font as shipped; the primary oracle.                                                                                               |
| 2   | `ss02`           | ss02             | Single set; Manual pins at `the-manual.html` lines 1937, 2567, 3604.                                                                   |
| 3   | `ss03`           | ss03             | Single set; Manual pin at line 4118.                                                                                                   |
| 4   | `ss04`           | ss04             | Single set; Manual pin at line 3471.                                                                                                   |
| 5   | `ss05`           | ss05             | Single set; Manual pin at line 3537.                                                                                                   |
| 6   | `ss06`           | ss06             | Single set; gapped ·Owe (the Manual’s ss06 div is CSS-visual only — no shaped pin exists, so the baseline is its first shaped record). |
| 7   | `ss07`           | ss07             | Single set; Manual pin at line 1623.                                                                                                   |
| 8   | `ss10`           | ss10             | Single set; Manual inner-span pin at line 3969.                                                                                        |
| 9   | `ss02+ss03`      | ss02, ss03       | Declared multi-set combination: both gate qsTea entry stances, so interaction is plausible.                                            |
| 10  | `ss06+ss07`      | ss06, ss07       | Declared multi-set combination: both reshape qsOwe.                                                                                    |
| 11  | `ss02+ss03+ss05` | ss02, ss03, ss05 | The §7 conformance-matrix example (“e.g. ss02+ss03+ss05 on ·Tea”); all three touch qsTea’s capability matrix.                          |

No combination is declared in the corpus today; rows 9–11 are this plan’s declarations, recorded here so the future conformance gate inherits them. Cost of the three extras is ≈90 s of shaping — negligible.

## 6. The equivalence triage

§3.4 defines `word: initial` ⇔ the left context is an edge, a space, or a ZWNJ — so in the new model, post-ZWNJ ≡ word-initial **and** post-space ≡ word-initial hold by definition, and symmetrically pre-boundary ≡ word-final via run-splitting. Today those alignments are maintained by hand and known-incomplete (the font fires `.noentry` rules against literal `uni200C`; `qsExcite` guards on space/ZWNJ/namer-dot). §13.1 requires checking them against the baseline so divergences surface as triage rows, never silent changes.

Four checks, run for **every configuration** over every basis string `w`:

| Check           | Eligible `w`                | Comparison                                                                                  |
| --------------- | --------------------------- | ------------------------------------------------------------------------------------------- |
| `zwnj-vs-edge`  | first symbol not space/ZWNJ | shape `ZWNJ + w` in one buffer; the `w` portion (clusters ≥ 1) vs. the baseline row for `w` |
| `space-vs-edge` | first symbol not space/ZWNJ | shape `space + w`; same comparison                                                          |
| `edge-vs-zwnj`  | last symbol not space/ZWNJ  | shape `w + ZWNJ`; the `w` portion vs. the baseline row for `w`                              |
| `edge-vs-space` | last symbol not space/ZWNJ  | shape `w + space`; same comparison                                                          |

The namer dot does **not** split runs (§3.4), so no namer-dot-vs-edge equivalence is asserted — namer-dot contexts are ordinary basis rows. The `w`-side of every comparison is the already-extracted baseline row (no re-shaping), so the cost is one extra shape per check per eligible string: ≈4 × 4.77M ≈ 19M shapes per configuration, ≈3.5 min single-threaded, ≈25 s on 10 workers.

A divergence is any difference in the `w` portion’s glyph names, cluster structure, or seam classifications (positions are compared but position-only differences are flagged separately, since an attachment shift without a glyph change is a different severity). Divergences are appended to `rebuild/out/equivalence-triage.tsv`, sorted like the baseline, one row per (config, check, string):

```text
config  check  codepoints  baseline_glyphs  boundary_glyphs  first_divergent_position  baseline_seams  boundary_seams  divergence_kind
```

`divergence_kind` ∈ {`glyph`, `seam`, `position-only`}. These are **triage rows, not errors** — expected hot spots are the `.noentry` universe behind ZWNJ, qsExcite’s boundary guards, and the namer dot’s lowered form (which counts space, ZWNJ, and run edge all as word-initial, so it should agree — disagreement there would be news). The summary records per-check, per-config divergence counts so the migration’s “true by definition in the new model” claim has a measured before-picture.

## 7. Validation

Two layers, both must pass before Phase 4’s outputs are trusted.

### Corpus pin replay

Collect every data-expect run from the three corpora (`site/index.html`, `site/the-manual.html`, `site/extra-senior-words.html`) using the existing collector and parser imported read-only from `test/test_shaping.py` / `conftest.py` (recon item 4: `parse_expect`, `_DataExpectCollector`, `run_shaping_test_runs` are import-safe). For every senior-variant run whose input sequence consists solely of basis-alphabet symbols (44 runes, space, ZWNJ, namer dot — runs containing other Latin literals are out of scope and skipped, with a count reported):

- Shape the run’s full sequence through the **extractor’s own library path** (same shaper, same classifier, same configuration dict derived from the run’s `data-stylistic-set`) and assert the pin’s per-seam expectations: join-at-height tokens (`~x~`/`~b~`/`~t~`/`~6~` = y5/y0/y8/y6), bare-adjacency joins (height unasserted, classification must be a join), `|`/`|?|` breaks (classification must be `break`), and `+`/`+?`/`+|` ligation per the parser’s interpretation expansion. Any disagreement is an **extractor bug until proven otherwise** — the pins are ground truth the existing suite already enforces against this exact font.
- For runs of length ≤ 4, additionally assert the baseline table row for that exact string and configuration matches the live shaping byte-for-byte (glyphs, clusters, seams, positions) — this pins the serialization path, not just the shaping path.
- For runs of length > 4, also look up each embedded length-4 window’s baseline row and report (not fail) any seam-classification difference between the long-context shaping and the window row, labeled as depth-2-horizon findings; these quantify §1’s accepted incompleteness rather than indicting the extractor.

The replay result (pins checked / skipped / disagreements) is recorded in `SUMMARY.md`; the disagreement count must be zero to proceed.

### Extractor unit tests

`rebuild/test_baseline_extract.py`, runnable as `uv run pytest rebuild/ -n auto --dist worksteal` (and picked up by a plain `uv run pytest rebuild/`). Coverage:

- GPOS discovery: exactly four cursive lookups behind `curs`; heights resolve to pixel {0, 5, 6, 8}; anchor-Y uniformity assertion.
- Name recovery: a known >63-byte compiled name resolves correctly via `TTFont.getGlyphName` (and would be truncated via `glyph_to_string`, proving the workaround is load-bearing).
- Cluster alignment: a known ligating pair (e.g. ·Day·Utter) produces one glyph covering two input positions and a `lig` seam; a known non-ligating pair does not.
- Classifier spot checks against a handful of corpus-pinned facts (a y5 join, a y0 join, a break, an ss-gated join that only appears under its configuration).
- Determinism: extracting the same shard twice yields identical bytes; row order matches the §3 definition; header content is complete.
- Equivalence checker: the `w`-portion comparison logic on synthetic cases (identical ⇒ no row; injected difference ⇒ row with correct `divergence_kind`).
- Split-buffer sampler: fixed-seed sample is stable across runs.

No test pins an outcome we have not already verified from the corpus or the recon — the baseline’s job is to record current behavior, not to assert what it should be.

## 8. Module and file layout

Everything new lives under `rebuild/`; nothing outside it is modified except the one-line `rebuild/out/` addition to `.gitignore`. Two implementers can work in parallel along the seam drawn below: implementer A owns the extractor (alphabet, shaper, classify, extract, cli), implementer B owns validation (equivalence, corpus replay, unit tests); both import the same row model and shaping primitives, which land first.

```text
rebuild/
  BASELINE-PLAN.md                 this document
  recon/baseline-recon.md          Phase 1 findings
  baseline/
    __init__.py
    model.py                       shared first: Row/Seam dataclasses, config tokens, row ordering, TSV serialization + parsing, header rendering
    alphabet.py                    the 47-symbol alphabet (codepoints + names), basis enumeration, shard partitioning by first symbol
    shaper.py                      per-process state: hb.Font + TTFont + one reused hb.Buffer; shape(text, features) -> (names, clusters, positions); split-buffer shaping
    classify.py                    GPOS curs-lookup discovery, per-height entry/exit sets, classify_seam(left, right) -> token
    extract.py                     orchestration: multiprocessing pool, shard workers, deterministic merge, gzip writing, digest/summary generation
    equivalence.py                 the four §6 boundary checks and equivalence-triage.tsv writer
    corpus_replay.py               read-only import of test/ collector + parser; the §7 replay against library shaping and baseline rows
    cli.py                         argparse front end
  test_baseline_extract.py         the §7 unit tests
  out/                             generated, gitignored: baseline-<config>.tsv.gz, digests.tsv, SUMMARY.md, equivalence-triage.tsv, split-check-disagreements.tsv
```

Public interfaces (the parallel-work contract):

- `model.Row` — frozen dataclass: `codepoints: tuple[int, ...]`, `glyphs: tuple[str, ...]`, `clusters: tuple[int, ...]`, `seams: tuple[str, ...]`, `positions: tuple[tuple[int, int, int], ...]`; `Row.to_tsv() -> str`, `Row.from_tsv(line) -> Row`, `row_sort_key(row)`; `CONFIGS: dict[str, dict[str, bool]]` (the §5 list, ordered).
- `shaper.Shaper` — `Shaper(font_path)`, `shape(text: str, features: dict[str, bool]) -> ShapeResult` (names via TTFont, clusters, positions), `shape_split(text, split_offsets, features)`.
- `classify.SeamClassifier` — `SeamClassifier(font_path)`, `heights() -> tuple[int, ...]`, `classify(left_glyph: str, right_glyph: str) -> str`.
- `extract.extract_config(config_token: str, out_dir: Path, workers: int) -> Digest` and `extract.run_all(out_dir, workers)`.
- `equivalence.run(config_token, baseline_path, out_path)` and `corpus_replay.run(out_dir) -> ReplayReport` — both consume `model.Row.from_tsv`, so the validation suite and the extractor cannot drift apart on the row format.

CLI (all via `uv run`):

```sh
uv run python -m rebuild.baseline.cli extract --config default --out rebuild/out --workers 10
uv run python -m rebuild.baseline.cli extract --all --out rebuild/out --workers 10
uv run python -m rebuild.baseline.cli equivalence --all --out rebuild/out
uv run python -m rebuild.baseline.cli replay --out rebuild/out
uv run python -m rebuild.baseline.cli summarize --out rebuild/out
```

Implementation-order note: `model.py`, `shaper.py`, and `classify.py` land first (with their unit tests), then A and B proceed in parallel. `make prettier` runs after every Python change; nothing is committed or staged without explicit user approval.
