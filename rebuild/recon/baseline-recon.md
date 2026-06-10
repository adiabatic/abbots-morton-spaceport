# Baseline extraction recon (§13.1)

Recon for the depth-2 baseline extraction: shape the basis through the current built Senior Sans font (`site/AbbotsMortonSpaceportSansSenior-Regular.otf`) and record every window's resolved outcome as a diff-stable table under `rebuild/out/` (which must be added to `.gitignore` when the build step lands; it is not there yet — `grep rebuild .gitignore` is empty). All findings below carry file:line references into the existing repo; nothing existing was modified.

## 1. The shaping toolkit

### Shaping and full glyph-name recovery

- `test/quikscript_shaping_helpers.py:21-51` — the canonical shape loop: one cached `hb.Font` (`_font()`, lines 21-25), one module-level reused `hb.Buffer` (`_BUF`, line 40) with `clear_contents()` + `add_str()` + `guess_segment_properties()` + `hb.shape()`, then GID → name via fontTools. The buffer-reuse invariant (materialize `glyph_infos`/`glyph_positions` before returning) is documented at line 39 and again at `test/test_join_ink.py:87`.
- **The 63-byte workaround**: HarfBuzz's `font.glyph_to_string` truncates glyph names to 63 bytes (CFF1 name budget — see the comment at `tools/quikscript_ir.py:671`), and this font has compiled names well past that. So every test resolves GIDs through a parallel `TTFont`: `shaped_glyph_name` at `test/test_shaping.py:377-379` (`_tt_font(variant).getGlyphName(gid)`), mirrored by `_gid_to_full_name` at `test/quikscript_shaping_helpers.py:34-35` with the explanatory comment at line 30. The baseline extractor must do the same — never trust `glyph_to_string`.
- Feature-scoped shaping: `_shape_with_features` at `test/quikscript_shaping_helpers.py:69-80` passes a `dict[str, bool]` (e.g. `{"ss03": True}`) as `hb.shape(font, buf, features)`. The corpus runner builds that dict from `data-stylistic-set` at `conftest.py:143-145` (`{f"ss{ss.zfill(2)}": True for ss in stylistic_set.split()}`).
- Cluster recovery for ligature-aware alignment: `_shape_with_clusters` at `test/quikscript_shaping_helpers.py:54-66` — each glyph's cluster is the index of the earliest input codepoint it covers; clusters are monotonic, which is how a caller maps output glyphs back to input rune positions even when a ligature ate two runes. The baseline's window→outcome alignment should use clusters, not naive index math.

### Cursive attachment positions

- `_shape_text_glyph_names` at `test/test_shaping.py:695-707` returns parallel `names, positions` lists; positions are HarfBuzz `glyph_positions` records with `x_offset`, `y_offset`, `x_advance`, `y_advance` (compared field-by-field in `_positions_equivalent`, `test/test_shaping.py:760-766`).
- Pen-position math: `_origin_xs` at `test/test_join_ink.py:103-109` accumulates `pen + pos.x_offset` per glyph, advancing by `x_advance` — that is the glyph drawing origin in font units. Bitmap column 0 sits at `_bitmap_origin_x_offset` (`test/test_join_ink.py:75-84`): `(hmtx_advance − bitmap_width·50) // 2`, mirroring the centering in `tools/build_font.py`. `PIXEL_SIZE = 50` font units per pixel (`test/test_join_ink.py:28`).

### The split-buffer technique

- `_isolation_glyphs_split` at `test/test_shaping.py:710-735`: shape each segment of the text in its own HarfBuzz buffer (split at given char offsets) and concatenate names/positions — by construction no contextual lookup can fire across a split. `_check_break_isolation` (`test/test_shaping.py:769-866`) uses it to verify every pinned break: the glyph at each token flanking the break must match between full and split shaping (or at least have identical outline signature + position, lines 828-846). ZWNJ injection was explicitly rejected as an isolation reference because the font fires `.noentry` rules against literal `uni200C` (comment at lines 784, 788).
- For the baseline this is the tool for the §3.4 intended-equivalence assertion: "post-ZWNJ ≡ word-initial" means shaping `ZWNJ + w` and comparing the post-ZWNJ glyphs against shaping `w` in a fresh buffer (run-initial). Divergences are triage rows, not errors.

### The join/break decision procedure (what the baseline should reuse)

The classifier everywhere in the test suite is **anchor-Y intersection, not ink contact**:

- `_try_interpretation` at `test/test_shaping.py:530-579`: for adjacent shaped glyphs `left, right`, compute `common_ys = {exit anchor Ys of left} ∩ {entry anchor Ys of right}`. Joined-at-height-h ⇔ `h ∈ common_ys`; joined (height unasserted) ⇔ `common_ys` nonempty; break ⇔ `common_ys` empty. Same procedure in `_pair_join_ys` at `test/quikscript_shaping_helpers.py:204-207` via `_entry_ys`/`_exit_ys` (lines 154-165; entry Ys union `meta.entry` and `meta.entry_curs_only`).
- The anchor sets come from the compiled YAML metadata (`build_anchor_map` at `test/test_shaping.py:382-399`, which calls `compile_glyph_set(load_glyph_data(...), variant)`), **not** from the font binary. For a zero-archaeology baseline the same facts are readable black-box from the built font's GPOS: the `curs` feature compiles to four LookupType-3 cursive lookups, **one per join height** — lookup 12: y=0 (455 records), 13: y=250 (x-height, 445), 14: y=300 (19), 15: y=400 (top, 31); font units / 50 = glyph-space y (0, 5, 6, 8). Verified empirically against the shipped OTF. So: _adjacent pair joins at height h iff the left glyph has an ExitAnchor and the right glyph has an EntryAnchor in the same per-height curs lookup; break iff no such lookup pairs them._ This is exactly equivalent to the tests' anchor-Y intersection (per-height lookups are why a y5 exit can never cursive-attach to a y0 entry).
- `test/test_join_ink.py` is **not** an alternative classifier — it is a defect detector layered on the same classifier. `_intended_join_ys` (`test/test_join_ink.py:155-172`) tags each intended Y as `cursive` (anchor intersection) or `stranded-exit`/`stranded-entry` (an `ex-ext-N`/`en-ext-N` extension suffix whose Y has no partner anchor), then `_check_ink_gap_at_y` (lines 112-152) measures the rendered pixel gap (`right ink left edge − left ink right edge` at the join row, using `exit_ink_y` fallback at line 127 and the entry-trim parent-bitmap fallback at lines 130-140); gap > 0 px or non-integer-pixel is a failure, ≤ 0 is fine. The baseline table should record the anchor-classified outcome (join height or break); the ink-gap arithmetic belongs to the §9 `E-UNREALIZED` detector, not the baseline columns.

## 2. The alphabet and boundary tokens

- **44 Quikscript runes, not 45**: `doc/glyph-names.md` lists U+E650–U+E66C (29 letters, ·Pea…·Exam) plus U+E670–U+E67E (15, ·It…·Ooze). `_plain_quikscript_letters` (`test/quikscript_shaping_helpers.py:98-109`) filters `postscript_glyph_names.yaml` to the same 44 (excluding `qsAngleParenLeft`/`qsAngleParenRight`, U+E66E/E66F, which are punctuation). The `test/test_join_ink.py:3` docstring's "45-entry context set" is stale; the measured count is 44.
- Boundary tokens: `BOUNDARY_TOKENS = ("space", "ZWNJ")` with chars `" "` and `"‌"` at `test/quikscript_shaping_helpers.py:112-114`; `_context_chars()` (lines 117-122) is the 46-symbol sweep alphabet the existing pair sweeps use.
- **The namer dot must be in the basis alphabet.** It is `periodcentered`, U+00B7 (`glyph_data/punctuation.yaml:1103`), with a calt-substituted `periodcentered.lowered` variant (`glyph_data/punctuation.yaml:1110`, behavior pinned in `test/test_namer_dot.py`: lowers word-initially before short letters, stays plain before tall/deep and mid-word). Two reasons it is load-bearing for the baseline: (a) the namer dot's _own_ resolved outcome depends on the following rune's height class, and word-initial context (start of run, space, punctuation, ZWNJ all count — `test/test_namer_dot.py:70-74`); (b) at least one rune conditions on it — `qsExcite.exit_baseline_before_vertical` declares `not_after: [space, uni200C, periodcentered]` (`glyph_data/quikscript.yaml:2614`), the exact ·Excite-guards-where-·Utter-does-not distinction §3.4 cites. Per §3.4 the namer dot is a registered boundary token that does **not** split runs, so it belongs in the alphabet as a 47th symbol rather than only as a run delimiter.
- Basis alphabet: 44 runes + space + ZWNJ + namer dot = **47 symbols**; depth-2 windows ([resolved-left, self, raw-right, raw-right²], §6.1) need length-4 strings (47⁴ ≈ 4.88M) plus the shorter edge-of-run windows (47³ + 47² + 47 ≈ 106k, negligible).

## 3. Stylistic sets

GSUB features in the built Senior OTF (verified from the binary's FeatureList and matching `site/AbbotsMortonSpaceportSansSenior-Regular.fea` lines 1209, 1217, 1482, 1541, 1549, 29374, 29418): `calt`, `ccmp`, and **ss02, ss03, ss04, ss05, ss06, ss07, ss10** (no ss01, no ss08/ss09). GPOS: `curs`, `kern`, `mark`. All seven ss features affect Quikscript shaping (their lookups substitute qs* stances). Semantics, from `README.md:136-142`:

| Set  | Effect                                                                                |
| ---- | ------------------------------------------------------------------------------------- |
| ss02 | allow ·I·Tea to join at the Short height                                              |
| ss03 | allow ·Tea to be joined to at the x-height                                            |
| ss04 | allow ·It to join at baseline after ·Day and before ·Low                              |
| ss05 | allow `·Et ~b~ ·Tea ~b~ …` double baseline joins (older/manual-style)                 |
| ss06 | use gapped ·Owe (doesn't connect at the top)                                          |
| ss07 | allow ·Owe·Day to join at the x-height again (including before ·Day+Utter / ·Day+Eat) |
| ss10 | suppress all joins for the wrapped letter(s)                                          |

Manual-pin usage (`grep data-stylistic-set site/*.html` — only `site/the-manual.html`): cell-level pins at lines 1623 (07), 1937 (02), 2567 (02), 3471 (04), 3537 (05), 3604 (02), 4118 (03); inner-span pin at 3969 (10); plus a `<div data-stylistic-set="06">` at 4194 that the collector does **not** see (`_DataExpectCollector._TAGS = {"td", "span", "dd"}`, `test/test_shaping.py:267`) — it is CSS-visual only, so ss06 currently has no shaped pin. Every pinned configuration is a single set; the format supports multi-set (`stylistic_set.split()` at `conftest.py:145` and `test/test_shaping.py:308`), but none is declared today.

Per §10 tier 3 the baseline must therefore cover **10 configurations**: default (no ss), each of ss02/ss03/ss04/ss05/ss06/ss07/ss10 alone (this supersets every configuration the Manual pins use), and at least one declared multi-set combination — none exists in the corpus, so the baseline must declare one; ss02+ss03 is the natural candidate (both gate qsTea entry stances, so interaction is plausible), with ss06+ss07 (both reshape qsOwe) as a second worth considering.

## 4. The corpus pin format

`doc/data-expect.md` is the authoritative spec; the parser is `parse_expect` at `test/test_shaping.py:113-253` with token regex at lines 86-103.

- Tokens: `·LetterName` → `qsLetterName` (`·-ing` → `qsIng`, `·J'ai` → `qsJai`; `_letter_to_qs` at lines 106-110); `\X` literal char via `postscript_glyph_names.yaml` reverse map or `uniXXXX`; `◊space`/`◊ZWNJ` boundary glyphs (both map to glyph `space`, lines 97-101).
- Variant assertions: `.alt`/`.half` check compiled **traits**; anything else (`.noentry`, `.en-y0`, `.extended`, …) checks `compat_assertions`; `.!x` negates; `.∅` demands the exact bare glyph name (`_modifier_matches` at lines 411-414, `_expected_exact_glyph_name` at 431-434).
- Ligatures: `·Day+Utter` must ligate (metadata `sequence == (qsDay, qsUtter)`, line 427-428); `+?` may ligate (separated path unasserted); `+|` may ligate (separated path asserts a break); `_expand_maybe_ligatures` (lines 442-485) tries all 2ᴺ interpretations.
- Connections: bare adjacency = join at unasserted height; `~x~`/`~b~`/`~t~`/`~6~` = join at y 5/0/8/6 (`HEIGHT_MAP`, line 129); `|` = break **plus** the break-isolation invariant; `|?|` = break without the isolation invariant; `?` = unasserted.
- Validation = the §1 anchor-Y procedure per connection, plus a half-stance leak heuristic on breaks (lines 551-566) and the split-buffer isolation check for senior runs (`run_shaping_test_runs` at lines 943-1036, isolation at 1019-1036). Cells are collected from `site/index.html`, `site/the-manual.html`, `site/extra-senior-words.html` by `_DataExpectCollector` (lines 261-357; handles `force-junior` spans and inner `data-stylistic-set` spans as separate runs) and turned into pytest items in `conftest.py:88-154`.
- A replay pass against the baseline can reuse `run_shaping_test_runs` wholesale (it is import-safe; `_assert_expect_any` at `test/quikscript_shaping_helpers.py:234-257` shows the minimal embedding) — or, cheaper, assert that each pin's per-seam expectation (join height/break per adjacent pair) matches the baseline row for the corresponding window.

## 5. Performance

Measured on this machine (Apple Silicon, 12 logical cores), Senior Regular OTF, 20,000 random 4-rune strings, single thread, reused buffer:

| Variant                                       | Time    | Throughput     | Per shape |
| --------------------------------------------- | ------- | -------------- | --------- |
| shape only                                    | 0.197 s | ~101,600 /s    | 9.8 µs    |
| shape + full-name recovery (TTFont)           | 0.211 s | ~94,800 /s     | 10.6 µs   |
| shape + names + position extraction           | 0.209 s | ~95,600 /s     | 10.5 µs   |
| shape + ss feature dict + names + positions   | 0.219 s | ~91,500 /s     | 10.9 µs   |

Name recovery and position extraction together add well under 1 µs/shape (within run-to-run noise); the feature dict adds ~0.4 µs. Implications for sizing the basis honestly:

- One full depth-2 basis (47⁴ ≈ 4.88M length-4 windows) ≈ **53 s single-threaded** per configuration; ×10 configurations ≈ 9 minutes single-threaded.
- The repo's parallelism pattern is pytest-xdist subprocess workers (`make test` = `uv run pytest test/ site/ -n auto --dist worksteal`, `Makefile:30-31`; helpers note at `test/quikscript_shaping_helpers.py:38` that each xdist worker is its own subprocess, so module-level `hb.Font`/`TTFont`/buffer caches are per-worker-safe). The extractor should mirror that: `multiprocessing` with each worker building its own `hb.Font` + `TTFont` and reusing one buffer — on 12 cores the full 10-configuration basis lands around **1-2 minutes**. Sharding by first symbol (47 shards) matches the existing `test_join_ink.py` parametrization pattern (`test/test_join_ink.py:255-256`).
- The post-ZWNJ ≡ word-initial assertion roughly doubles the windows it touches (each checked window shaped once with a ZWNJ prefix, once split); still minutes, not hours.
- Practical gotchas baked into the numbers: reuse one `hb.Buffer` per worker (fresh buffers per shape are measurably slower) and materialize `glyph_infos`/`glyph_positions` before the next shape (`test/quikscript_shaping_helpers.py:39`).

## 6. Glyph name → (family, stance/modifiers) mapping

The naming grammar (forward direction) is `_compiled_family_glyph_name` at `tools/quikscript_ir.py:714-720`: `"." .join([family_name, *traits, *modifiers])`. The sanctioned reverse parse is `_split_family_compiled_name` at `tools/quikscript_ir.py:723-749` (longest-family-name match, then each dot token classified as trait vs modifier via `_SOURCE_FAMILY_TRAITS`).

Empirical census of the shipped Senior OTF (1148 glyphs, 757 `qs*`):

- **Base**: `qsFamily` (44 families) or ligature `qsX_qsY` — 13 ligature bases: qsDay_qsEat, qsDay_qsUtter, qsJai_qsUtter, qsJay_qsUtter, qsOut_qsTea, qsSee_qsEat, qsSee_qsUtter, qsTea_qsOy, qsThey_qsUtter, qsThey_qsZoo, qsVie_qsUtter, qsWay_qsUtter, qsWhy_qsUtter.
- **Traits** (immediately after the base): `alt` (56 glyphs), `half` (60).
- **Anchor-identity modifiers**: `en-yN` (390), `ex-yN` (437), `ex-yN-right` (4), `ex-noentry` (13), `noentry` (203), `noexit` (1), `ex-dips` (4), `nonjoining-left` (2).
- **Contextual labels**: `after-*` (after-baseline-letter, after-day, after-fee, after-i, after-it-and-vie, after-no-baseline-join, after-see, after-tall, after-xheight-exit, after-ye, after-zoo), `before-*` (before-day, before-day-exam, before-fee, before-low, before-may, before-other, before-utter, before-vertical), plus shape labels `reaches-way-back` (12), `smaller-loop` (8), `gapped` (1).
- **Derived extension/contraction/trim suffixes** (generated late in the build): `en-ext-N` (174), `en-ext-N-at-N` (14), `en-con-N` (54), `en-trim-N` (14), `ex-ext-N` (285), `ex-con-N` (161).

Example: `qsNo.alt.en-y0.ex-y0.after-it-and-vie.en-ext-1.ex-con-1` = family qsNo, trait alt, entry y0, exit y0, contextual label, entry extended 1 px, exit contracted 1 px.

For outcome columns, prefer **not** hand-parsing names at all: `compile_glyph_set(load_glyph_data(GLYPH_DATA_DIR), "senior").glyph_meta` (the `build_anchor_map` path, `test/test_shaping.py:382-399`) maps every full compiled name to a `JoinGlyph` (`tools/quikscript_ir.py:60-100`) carrying `base_name`, `family`, `sequence` (ligature components), `traits`, `modifiers`, `compat_assertions`, `entry`/`entry_curs_only`/`exit` anchors, `generated_from`, and `transform_kind` — structured fields the tests already treat as authoritative. Name parsing (legitimate for this black-box extraction per the workflow rules) is then only a fallback/cross-check; if used, follow `_split_family_compiled_name`'s longest-match-then-classify procedure rather than regexes, because family names are prefixes of nothing but themselves only after longest-match (`qsOut` vs `qsOut_qsTea`).

## Baseline-shape recommendations distilled

- Outcome row per window: resolved full glyph name for `self` (plus its structured (family, traits, modifiers) projection), seam state toward the right neighbor classified by the per-height curs-lookup intersection (height h or break), and the GPOS-applied position deltas if the diff is to be position-sensitive. Sort rows lexicographically by (configuration, window symbols) for §8 diff-stability; TSV like the design doc's `build/settlement.tsv`.
- 10 ss configurations (§3 above); 47-symbol alphabet including the namer dot; length ≤ 4 windows.
- Intended-equivalence pass: for every window, post-ZWNJ shaping vs fresh-buffer word-initial shaping; record divergences as triage rows, not failures.
