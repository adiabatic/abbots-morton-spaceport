# M1 plan: the first runes and the §14 module skeleton

Decisions for milestone M1, stated as the build carries them out. The evidence behind them was `rebuild/recon/m1-families.md` (Recon A), `rebuild/recon/m1-integration.md` (Recon B), `prototype/PLAN.md`, `prototype/REPORT.md`, and `rebuild/BASELINE-REPORT.md`. Those files and the `prototype/` directory are deleted, so this plan’s Recon A, Recon B, and REPORT citations resolve only in git history. The design doc (`doc/rebuild-design.md`) is binding, and a bare section reference points into it. Two prototype follow-ups are requirements of this plan: the outcome-partition property is a hard build invariant (REPORT follow-up 1), and the settlement lookup’s Extension promotion is watched by the GSUB offset-headroom check (follow-up 2).

Rules for M1 work:

- New code goes under `rebuild/`.
- The old pipeline’s Senior Sans output stays byte-identical (SHA-256 `3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35`).
- Python runs through `uv run`, and broad test runs use `-n auto --dist worksteal`.
- Prose uses American English and never abbreviates “isolation” or “isolated”. Comments and docstrings are not hard-wrapped.
- YAML formatting follows `AGENTS.md` and `tools/reflow_yaml.py`.

## 1. Locations

**Rune files: `glyph_data/runes/`**, the address §2 gives them. The old pipeline finds YAML only through the non-recursive `path.glob("*.yaml")` in `tools/build_font.py`’s `load_glyph_data`, so the old font build never reads files in `glyph_data/runes/`. Each migrated family has one file named for its rune; a ligature’s file is named for its sequence (`qsTea_qsOy.yaml`).

**Registry: `rebuild/script.yaml`, a recorded deviation from §2 until cutover.** A `glyph_data/script.yaml` would break the old build: `load_glyph_data` reads any document without one of its `_STRUCTURAL_KEYS` as a Senior kerning rule, and `generate_kern_fea` then fails with a `KeyError` on it. The registry stays under `rebuild/` and moves to `glyph_data/script.yaml` at cutover. `spec_load.load_spec` takes the registry path as an argument, so the move changes `spec_load.DEFAULT_REGISTRY_PATH` and the few other places that name the path (`git grep '"script.yaml"'` lists them).

**Schemas: `rebuild/schema/rune.schema.json` and `rebuild/schema/script.schema.json`** (JSON Schema 2020-12). `spec_load` validates every rune file and the registry against them at load time, with its own evaluator (§4 of this plan). `.vscode/settings.json` maps both schemas to their files for editor validation.

**Modules: `rebuild/pipeline/` as a package**, named after §14:

```text
rebuild/pipeline/
  __init__.py
  model.py            shared frozen dataclasses that all three groups below code against; the kernel crate lists their fields by hand
  spec_load.py        YAML → ResolvedSpec; schema validation; lints (naming, ductus parity, right.then, dead reference)
  surface.py          cell enumeration; binding resolution; pairings/unlocks/scopes → CellPlan
  settle.py           the §6.1 settlement vocabulary: tokens, boundary cells, guarded formation; the settlement function itself is in the kernel crate
  table.py            the decision- and join-table data model and the readers for the crate's table artifacts
  geometry.py         per-cell bitmap/anchor realization; stubs; bindings; extensions; gap arithmetic
  defects.py          E-DANGLE, E-UNREALIZED, E-ANCHOR, off-anchor contact, dead policy
  emit_gsub.py        the staged GSUB (its module docstring gives the stage order)
  emit_gpos.py        per-height curs lookups
  compile_font.py     mini-font build via build_font(senior_fea=...) and the pack_gsub repack
  readback.py         post-compile read-back: the written font re-parsed and checked against the plan, GSUB offset headroom included
  conform.py          HarfBuzz sweep against settlement, the per-row baseline comparison and its memoized walk
  oracle.py           baseline-oracle driver, divergence classifier, ledger matching (outside the tables' stamp)
  oracle_positions.py the position comparison: kern normalization, the mismatch diff, the served-position codec and verifier, the sidecar evaluator, the shaper factory (outside the tables' stamp and the row stamp)
  explain.py          the §6.3 item (a) CLI (python -m rebuild.pipeline.explain)
  baseline_subset.py  streaming filter of rebuild/out/baseline-*.tsv.gz to the migrated alphabet; refilters when the alphabet or the sources change
  coretext_smoke.py   CoreText-vs-HarfBuzz comparison over the extended sequence set
```

The package holds more modules than this skeleton, among them `kernel_exec.py` (the crate driver), `run_m1.py`, `witness.py`, `pack_gsub.py`, `manual_pins.py`, and `fingerprint.py`. Each module docstring states its role.

Tests are `rebuild/test_<module>.py`. Pytest’s `testpaths` excludes `rebuild/`, so `make test-rebuild` runs them.

`rebuild/pipeline/run_m1.py` is the integration driver (`uv run python -m rebuild.pipeline.run_m1`), and its module docstring gives the order of its stages. Font-side conformance runs through `run_m1 --conform-only` (with `--jobs`, one process per unit: the ss10 overlay whole and each settlement configuration once per final symbol, `run_m1.sweep_units`; maximum length from `--conform-max-length`, default 4), which the artifact cycle runs as `gate:conform`. `make conform-deep` runs the same sweep at maximum length 5+ under its own green record. The CoreText smoke runs through `uv run python -m rebuild.pipeline.coretext_smoke`.

The build writes its artifacts to `rebuild/out/m1/` (gitignored with `rebuild/out/`): `M1.otf`, `M1.fea`, `settlement-<config>.tsv`, `joins-<config>.tsv`, the per-gate summaries (`conform_summary.json`, `readback_summary.json`, `oracle_summary.json`, and others), `divergence-audit.tsv`, and the filtered baseline sub-tables. Three checked-in, human-reviewed source files feed the gates: the divergence ledger `rebuild/m1-divergences.yaml`, the alias map `rebuild/m1-aliases.yaml`, and the defect gates’ allow-list `rebuild/m1-contact-allow.yaml`. The allow-list’s header gives the signature forms it takes; most of its entries are off-anchor contacts the old font already draws on a join the baseline shows. `make prettier` runs black over the repository, `rebuild/` included (line length 110). `rebuild/REVIEW-PLAN.md` specifies the review corpus that reads the divergence ledger.

## 2. M1 scope

**The first batch was qsPea, qsTea, qsMay, qsIt, qsOy as a fully modeled fifth rune, and the qsTea_qsOy ligature.** Each later batch adds one letter and any ligatures it completes. `rebuild/pipeline/baseline_subset.M1_ALPHABET` lists the migrated alphabet and `glyph_data/runes/` holds the rune files.

Why the first batch took this shape: Recon A found that the four families qsPea, qsTea, qsMay, and qsIt are formation-closed with no ligatures. The qsTea_qsOy ligature was added anyway, because the predecessor's unjoined exit before an entryless ligature (§5.7) tests the architecture more than any other available behavior, and the prototype had already shown that it works. Its trailing component qsOy is outside the four. The plan chose to bring qsOy’s rune file in as a full fifth rune instead of restricting the conformance alphabet to “qsOy only immediately after qsTea”. qsOy is small (a bare form, one x-height-entry stance, and its locked copy). Modeling it fully keeps the conformance gate total over the alphabet with no exceptions, where the restriction would have needed new code and left a documented gap. It also adds baseline windows that must conform once qsOy is typeable: `·May ~x~ ·Oy`, `·Pea | ·Oy`, and `·Tea+Oy`, all verified against the baseline. The prototype modeled qsOy as inert (its deviation 4). M1 models it fully, so qsOy’s real joins are conformance obligations. qsOut_qsTea was left out of the first batch because its interesting case, the after-·See `bind:` shape, needs qsSee, and without qsOut in the alphabet it could never form.

**Conformance alphabet:** the three boundary tokens (`0x0020` space, `0x00B7` namer dot, `0x200C` ZWNJ) plus one code point per migrated family. `baseline_subset.M1_ALPHABET` is the list. The alphabet is kept formation-closed: every ligature whose components are both in the alphabet has a rune file.

**Configurations.** `baseline_subset` filters every `rebuild/out/baseline-*.tsv.gz`, but the acceptance gate runs only on the configurations that can affect the migrated letters; `conform.ACCEPTANCE_CONFIGS` lists them. The reason for each: `ss03` (half-·Tea entry widening, qsMay’s ss03-gated exit extension toward qsTea, qsTea_qsOy forming before the marker lookups), `ss04` (qsIt’s baseline-pairing unlock), `ss05` (·Tea joining at the baseline on both sides), the declared combination `ss03+ss05` (multi-set union semantics and composite markers on qsTea), and `ss10` (the isolated-forms overlay, which touches every rune; `conform.OVERLAY_CONFIGS`). `ss06`, `ss07`, and `ss06+ss07` change only unmigrated families. Whenever `baseline_subset.refresh` refilters, it checks that their sub-tables (`DEFAULT_COVERED_CONFIGS`) are row-identical to `default`’s, so the default run covers them. The check runs at refilter time because only a refilter can change the answer. A configuration that diverges is never stamped fresh, so its `SubsetIdentityError` repeats on every run until that configuration moves into `conform.ACCEPTANCE_CONFIGS`.

**The ss10 overlay is modeled, not settled.** The emitter’s ss10 input substitution replaces every letter’s cmap glyph with its anchor-free `.ss10` copy before formation, so under ss10 nothing forms, settles, or attaches, and the configuration has no settlement table (`conform.OVERLAY_CONFIGS`). The conformance sweep covers it to `conform.OVERLAY_MAX_LENGTH` behind read-back’s isolation check, and the oracle compares its rows with the bare stream (every letter its default-stance cell, every junction a break). The old font’s ss10 isolates every letter the same way, through its own anchor-free `.ss10` copies, so the classifier gives no class to an ss10 row off a boundary, and any such divergence waits for review.

**Kerning is outside M1 settlement.** The sidecar kerning records for the migrated families move over through §12 later, and the mini-font emits no kerning. The oracle’s position comparison therefore evaluates `glyph_data/senior_quikscript_kerning.yaml` read-only over the migrated pairs and adds each expected kern back before diffing. Any remaining position divergence is real. Position-only divergences that the position comparison attributes to a kern or a ZWNJ adjacency match the `kern-out-of-scope` ledger entry, whose status `triaged` means they are never accepted automatically (§12 keeps ZWNJ kerns as a proven pattern).

**The namer dot is in scope as a token.** M1’s emitter supplies its own dot-lowering stage, because `_namer_dot_calt_fea` does nothing on the `senior_fea` path (Recon B), and the glyph set includes `periodcentered` and `periodcentered.lowered`. No rune record conditions on `is: namer-dot`, so that condition value is registered but unexercised. The dot lowers with ZWNJ transparent to the match, as in the old font (baseline row `00B7:200C:E670`), so the split-buffer check treats the two dot forms as one slot signature, and the oracle’s name comparison folds `periodcentered.lowered` into `periodcentered`.

## 3. The rune-file template

A composite skeleton showing every §3 construct the migrated runes need. No single rune uses all of it; the annotations name the rune that uses each construct. Formatting rules appear as comments where they apply: double-quoted bitmap rows; bare trailing `#` markers on the rows whose glyph-space y is 5 and 0 (which rows those are depends on `y_offset`); short lists inline; family lists in code-point order (qsPea, qsBay, …, qsOoze); `when:` always written out; no hard-wrapped prose.

```yaml
rune: qsMay
codepoint: 0xE665                  # a ligature file declares `sequence: [qsTea, qsOy]` instead, with no codepoint
ductus:                            # closed set of motions: every stance names one, and every motion has a stance; see §8 of this plan
  loop: |                          # DRAFT — pending author sign-off
    Written clockwise, starting from the leftmost pixel at the baseline. It continues right, loops around underneath the baseline, and then exits at the x-height on the right.
  grounded-loop: |                 # DRAFT — pending author sign-off
    As the loop, but the final stroke stays down and rests on the baseline at the right.
notes: |
  Optional prose. Join constraints go in pairings, not here.
mono: {bitmap: ["..."], y_offset: -3}    # the drawing for the mono font only; no anchors
stances:
  loop:
    motion: loop                   # build error if missing or not a ductus key
    traits: []                     # [half] / [alt] stay for data-expect compatibility (qsPea.half, qsTea.half)
    bitmap:                        # the isolated drawing; a Deep letter has 9 rows and y_offset -3
    - "  ### "
    - " #   #"                     # (rows abbreviated in this template)
    - "#### #"  #
    - " ...  "
    - " ...  "  #
    - " ...  "
    - " ...  "
    - " ...  "
    - "  ##  "
    y_offset: -3
    bitmaps:                       # named hand-drawn alternatives, used only by the bindings below
      pulled-back-stubless: {bitmap: ["..."], y_offset: -3}
    surface:
      entries:
        baseline: {x: 0, stroke: horizontal}
        x-height: {x: 3, stroke: horizontal, joined: pulled-back-stubless, joined_x: 2, from: [{family: qsUtter}]}
          # `from:` is an allowlist (§13.3): this entry row joins only the named scope. The row's own height is implied, so the scope never repeats it as `joined_at: x-height`.
          # `joined:` is the side binding used when this side joins; `joined_x:` moves the anchor with the bound drawing. qsPea's dips use a stub instead:
          #   x-height: {x: 0, stub: {cols: [0], inks_when: joined}}  — ink in that row only when joined.
      exits:
        x-height: {x: 5, stroke: horizontal, unjoined: safe}
          # `unjoined:` is the drawing used when this side declines a join mid-word. `safe` is allowed only when the build verifies there is no reaching ink (qsIt's bar); qsMay's own x-height exit declares neither. qsPea.half's exit row carries a flagged oddity instead:
          #   x-height: {x: 4, ink_y: 6}            — the old font's exit_ink_y
          # An entry row that only registers a GPOS anchor carries `selectable: false` (the old font's entry_curs_only). No shipped rune uses it; the fixture spec's qsTea.half top entry does.
      pairings:
        never: [{entry: x-height, exit: x-height}]
          # qsIt uses only:, because its legal set is smaller than its complement:
          # only: [{entry: x-height, exit: baseline}, {entry: x-height, exit: none}, {entry: baseline, exit: x-height}, {entry: baseline, exit: none}, {entry: none, exit: x-height}, {entry: none, exit: baseline}, {entry: none, exit: none}]
      cells:
        - {entry: x-height, exit: baseline, bitmap: open-on-the-left, exit_x: 5}
          # an explicit row for one cell. qsOy carries this one to move the exit anchor with the joined drawing, and qsUtter.alternate carries another (reaches-way-back). qsPea's dip on both sides needs no row: its two stubs compose it.
      unlocks: []
          # qsTea.full carries: {pairing: {entry: baseline, exit: baseline}, feature: ss05} and {entry: x-height, feature: ss03, when: {left: {family: [...widened ss03 scope...]}}}
          # qsTea.half carries: {entry: x-height, feature: ss03, when: {left: {family: [...widened ss03 scope...]}}}
          # qsIt carries two ss04 rows, split around ·Cheer: {pairing: {entry: baseline, exit: baseline}, feature: ss04, when: {left: {except: [family: qsCheer]}}} and the same pairing with when: {left: {family: qsCheer}, right: {except: [family: qsThaw]}}
      require: []                  # for a stance that exists only when joined; qsFee.reversed-loop is the example (require: [entry])
  grounded-loop:
    motion: grounded-loop
    bitmap: ["..."]
    y_offset: -3
    surface:
      entries:
        x-height: {x: 2, stroke: horizontal, stub: {cols: [3], inks_when: unjoined}}    # the other stub polarity: ink in the base drawing that is removed when the side joins
      exits:
        baseline: {x: 4, toward: [{family: qsDay}, {family: qsSee}]}   # `toward:` is the exit-side allowlist (code-point order)
policy:
  order: [loop, grounded-loop]     # stance preference; stances left out follow in declaration order
  refuse:
    - {exit: baseline, when: {right: {family: [qsDay, qsThaw, qsZoo, qsYe, qsHe, qsNo, qsRoe, qsIt, qsEat, qsUtter, qsOoze]}}, why: These never receive ·May's baseline exit.}
    - {exit: baseline, when: {left: {family: qsRoe}, right: {family: qsEt}}}    # conditions on both sides when needed
      # refuse may not use right.then, because a refusal must be decidable one position to the left; the schema and a spec_load lint both enforce this
  prefer: []                       # yielding by default (cell: / over: / when:); mode: absolute outranks join count and needs a why:
  extend:
    - {exit: x-height, by: 1, ok: [1, 1], when: {right: {family: [qsDay, qsFee, qsJai, qsJay, qsRoe, qsIt]}}}
    - {exit: x-height, by: 1, when: {right: {family: qsTea}, feature: ss03}}    # the ss03-gated reach
    - {entry: baseline, by: 1, when: {left: {family: [qsPea, qsTea, qsYe, qsHe, qsIt]}}}
      # an entry-side extend, shown for its shape only (qsMay.yaml has none)
      # a record that grants or extends an entry at height H already fixes the junction at H, so the left scope never repeats it as `joined_at: H`.
      # qsIt's self: condition (the old font's extend_exit_when_entered):
      #   {exit: baseline, by: 1, when: {self: {entry: live}}}
      # side and height are required; `stance:` only when more than one stance offers that side and height (none of the three above needs it); records on the same side never sum; the most specific record wins (§6.2)
  contract:
    - {stance: loop, entry: x-height, bind: pulled-back-stubless, when: {left: {family: qsFee}}, why: ·Fee's long reach-over absorbs the baseline stub; the redraw spans rows, so it is a bound shape, not arithmetic.}
      # `bind:` substitutes a hand-drawn alternative for same-row arithmetic; `trim: N` blanks ink on the receiving side instead. `stance: loop` is needed here because both ·May stances offer an x-height entry. This record exists only in `rebuild/pipeline/fixtures.py`: qsMay.yaml's x-height entry row binds `pulled-back-stubless` for every enterer, so the rune needs no such contract (§5).
  resolve: []                      # settles a conflict between a prefer on this rune and one on another rune, as {against: {rune, id}, when, pick, why}; `migrated:` is optional and marks a resolve carried over wholesale from the old font
  groups: {}                       # rune-local sets: {union: [...], minus: [...]} over family literals, traits, classes
```

Scope a `from:` or `toward:` list from the baseline TSV, never from reading FEA. The old font joins a bare pair by GPOS cursive attachment alone whenever both bare glyphs carry anchors at the same height. No calt rule fires, so an FEA grep reports the pair as having no interaction, and a scope written from that reading drops real joins in both directions. The length-2 rows of `rebuild/out/m1/baseline-<config>.subset.tsv.gz` are the definitive pair-level join map.

### `rebuild/script.yaml` contents

Registries only, never policy (§2). The file is the authority; its shape:

```yaml
heights: {baseline: 0, x-height: 5, y6: 6, top: 8}
  # closed; qsPea's ·Pea·Pea chain makes y6 live, so all four curs lookups are emitted
boundary_tokens:
  space: {codepoint: 0x0020, splits_runs: true}
  zwnj: {codepoint: 0x200C, splits_runs: true}
  namer-dot: {codepoint: 0x00B7, splits_runs: false}    # addressable as `is: namer-dot`; never splits a run (§3.4)
features:
  ss03: {kind: capability, description: "x-height exiters reach ·Tea (full-size bar when a baseline-join follows, else the half stub)"}
  ss04: {kind: capability, description: "·It joins at the baseline on both sides at once, any neighbor"}
  ss05: {kind: capability, description: "·Tea joins at the baseline on both sides at once, any neighbor"}
  ss10: {kind: taste, description: "isolated forms overlay", overlay: isolated}
  interactions: [[ss03, ss05]]      # the declared combinations, each verified by conformance
predicate_classes:                 # derived only: computed expressions, never hand-listed members (§2)
  halves-that-exit-at-x-height: {all: [{trait: half}, {can_exit_at: x-height}]}
  can-enter-at-baseline: {can_enter_at: baseline}
  can-enter-at-x-height: {can_enter_at: x-height}
  can-exit-at-baseline: {can_exit_at: baseline}
  can-exit-at-x-height: {can_exit_at: x-height}
  talls: {height_class: tall}
  shorts: {height_class: short}
  deeps: {height_class: deep}
  # the `no_pea` variant of the halves set is written at the use site as class + except, not as a second class
families:                          # the full name registry, so conditions naming unmigrated families validate
  qsPea: {codepoint: 0xE650}
  qsBay: {codepoint: 0xE651}
  # ... every letter in code-point order, each ligature ({sequence: [...]}) after its lead ...
```

The `families` registry lets the dead-policy gate tell a **record waiting on unmigrated letters**, whose condition names only families that have no rune file yet, from an unused record. The gate lists records waiting on unmigrated letters and does not fail on them.

## 4. The JSON schema

Files: `rebuild/schema/rune.schema.json` and `rebuild/schema/script.schema.json` (draft 2020-12), the only source of the rules. `jsonschema` is not a project dependency, so `spec_load` validates against them with a small built-in evaluator (`spec_load._SchemaChecker`) that covers the keyword subset the files use and raises on any keyword it does not recognize. `test_jsonschema_agrees_with_builtin_checker` compares the two whenever `jsonschema` is importable (under `uv run --with jsonschema`). What the schema enforces:

- **The closed `when:` vocabulary.** `$defs.when` allows only `left`, `right`, `self`, `word`, and `feature`, with `additionalProperties: false` at every level. `left` allows `family`, `class`, `stance`, `joined_at`, `stroke`, `is`, `except`, and `bitmap`. `right` allows `family`, `class`, `stroke`, `is`, `except`, and `then`, where `then` is a hop to the next letter with the raw right axes only. A `then:` chain may reach at most `model.RIGHT_CHAIN_CAP` letters past the immediate right neighbor, and `spec_load` enforces that cap. `self` allows `{entry: live|none, exit: live|none}`. `word` is the enum `initial|medial|final|isolated`.
- **No `right.then` on `refuse`.** `refuse` records use `$defs/whenWindowDecidable`, whose `right` has no `then`. `spec_load` repeats the check in Python so the error message can cite the design rule (a refusal must be decidable one position to the left, §3.3).
- **The stance-ID rule.** Stance keys and motion names use `$defs/motionName`, which rejects the pattern `(before|after|noentry|noexit|nonjoining|ss[0-9])`. `spec_load` repeats it as a Python lint with a readable message. Generated display names are exempt, because no authored field holds one.
- **Structural shapes.** `pairings` is `{never: [...]}` and/or `{only: [...]}` of `{entry, exit}` pairs over registered heights and `none`. `cells:` rows require `bitmap:` (the explicit binding is the reason for the row) and allow `entry_x`/`exit_x`. `unlocks` rows require `feature:` and exactly one of `entry:`, `exit:`, or `pairing:`, with an optional narrowing `when:`. `extend` and `contract` require exactly one side. `spec_load` requires `stance:` whenever more than one stance offers that side and height, instead of guessing. `why:` is required on every `resolve` and on every `prefer` with `mode: absolute` (an `if/then`). `ductus` values are strings.
- **What the schema cannot express** is checked elsewhere. `spec_load` checks ductus parity, predicate-class derivability (no hand-listed cross-rune lists), and duplicate rune-local groups. `surface.resolve_cell` applies the cells resolution order (explicit `cells:` binding over side bindings over the base bitmap) and raises the error for two side bindings that disagree on a reachable cell with no explicit row. The crate’s `specificity.rs` computes extensional specificity, and `defects.run_gates` checks dead policy.

## 5. Module contracts

All shared types live in `rebuild/pipeline/model.py`, which all three groups below code against. The kernel crate lists the model's fields by hand, so a change to `model.py` must also be made in `rebuild/kernel-rs/`; the spec-echo parity test in `rebuild/test_kernel_io.py` fails until it is. Every policy record, surface row, and unlock carries `provenance: Provenance(file: str, path: str)` (the YAML file and key path), which `explain`, the TSV artifacts, and the FEA comments read.

```python
# model.py — the frozen contract (all dataclasses frozen, hashable where used as keys)
Height = str                                   # "baseline" | "x-height" | "y6" | "top" (registry-validated)

@dataclass(frozen=True)
class CellId:
    rune: str                                  # family name (ligatures are ordinary runes)
    stance: str
    entry: Height | None
    exit: Height | None
    adjustments: tuple[str, ...] = ()          # ordered, generated: ("en-ext-1",), ("locked",), () — never authored

@dataclass(frozen=True)
class Settled:
    cell: CellId
    junction: Height | None                        # the committed junction toward the next position
    extension: int                             # summed connector pixels this side carries on this junction

@dataclass(frozen=True)
class ResolvedSpec:                            # spec_load's output; the input to everything else
    runes: Mapping[str, Rune]                  # only the modeled runes (ligature files included)
    registry: ScriptRegistry                   # heights, boundary tokens, features + interactions, predicate classes (membership resolved), full family-name registry
    # Rune carries ductus, stances (with Surface: entry/exit rows incl. scopes/bindings/oddities, pairings,
    # cells, unlocks, require) and Policy (order, refuse/prefer/extend/contract/resolve, groups), all parsed
    # and scope-expanded but not yet geometry-resolved.
```

`model.py` holds more than this frozen block and documents each addition: the generated adjustments grammar (`locked`, `en/ex-ext-N`, `en/ex-con-N`, `en/ex-trim-N`, `en/ex-bind-<bitmap>`) that settlement writes and geometry reads; `relevant_marker_features`, `marker_glyph_name`, `locked_glyph_name`, and `raw_rename_map`, so the table builder, the emitter, and conform agree on marker-copy and locked-copy names without a cross-group import; and `CellPlan` and `GlyphRecord`, the two artifacts passed from one group to another.

Two narrowings of the model are deliberate. `Policy.groups` resolves rune-local groups to family-grain `frozenset[str]`, so a trait- or stance-qualified group atom (`{family: qsDay, trait: half}`) is widened to the bare family with a `SpecWarning`. Group 2’s matching sees families only, which is conservative for the ss04 veto carve-out, and the `SpecWarning` flags the first rune whose qualified atom needs to discriminate. `Condition` has no trait axis, so a trait qualifier in a condition is a load error (“not representable”) instead of a silent widening.

**The unjoined exit is an adjustment, not a height.** `CellId.exit` is `Height | None` with no unjoined token, so a mid-word declined exit whose row binds a named unjoined bitmap settles as exit `None` plus an `ex-bind-<bitmap>` adjustment, and geometry’s `bind` op draws it. An explicit `cells:` composition for `(entry-state, height-unjoined)` overrides the row binding. `unjoined: safe` rows collapse to the plain exit-none cell, and at a boundary the exit was never declined, so no token is emitted. `surface.resolve_cell` does not apply `unjoined:` side bindings to the token-less exit-none cell: that cell is the boundary rendering (the base drawing, dangling ink and all; the prototype’s anchor_kept_at_boundary). A live exit at a different height still takes the unjoined binding implicitly, which keeps the side-binding-disagreement build error in force. Surface, settlement, and geometry therefore treat the row’s unjoined binding and the token as one binding and never stack them.

### Group 1 — the spec front end (`spec_load`, `surface`, the schemas)

```python
# spec_load.py
def load_spec(runes_dir: Path, registry_path: Path, schema_dir: Path) -> ResolvedSpec: ...
    # schema validation per file (§4 of this plan); then Python lints: stance-ID regex, ductus parity (every
    # stance names a motion, every motion has a stance), refuse right.then rejection, the then-chain depth cap,
    # family references resolved against the registry, predicate-class evaluation, duplicate rune-local-group flag.
    # Collects every error, then raises SpecError(file, path, message, line=None, issues=None), whose
    # issues: tuple[SpecIssue, ...] holds each SpecIssue(file, path, message, line); a single-issue error
    # keeps the three-argument shape.
    # resolve: records load only in the against-a-named-record form ({against: {rune, id}, when, pick, why},
    # §5.8). The tiebreak form (at:) and grouped resolves are load errors, and a resolve's when: may not carry self:.
    # A pairings row naming a side its stance never declares is vacuous and raises a SpecWarning, not an error.

# surface.py
def enumerate_cells(spec: ResolvedSpec, rune: str, features: frozenset[str] = frozenset()) -> tuple[CellId, ...]: ...
    # declared rows ∪ {none} per side, filtered by pairings (never/only), require, and unlocks. Pairings filter
    # two-sided cells only (§3.2: one-sided and isolated cells always exist), so qsIt's only: rows that name
    # none sides are redundant but harmless.
def enumerate_cells_with_unlocks(spec, rune, features=frozenset()) -> tuple[tuple[CellId, tuple[Unlock, ...]], ...]: ...
    # the same cells, each tagged with the unlock records that grant it (an unlock's feature and when:
    # narrowing apply at settlement).
def unlocks_for_cell(spec: ResolvedSpec, cell: CellId) -> tuple[Unlock, ...]: ...
    # every unlock gating the cell; empty when the cell exists at default capability.
def resolve_cell(spec: ResolvedSpec, cell: CellId) -> CellPlan: ...
    # binding resolution: which named bitmap the cell uses (explicit cells: row > side bindings
    # (stub/joined/unjoined) > base), per-cell anchor x values (joined_x/entry_x/exit_x overrides), the
    # flagged oddities (ink_y, selectable), and the E-DANGLE obligation when a declined side has no
    # unjoined binding and is not verified safe (geometry does the verification). CellPlan.unlock holds the
    # first granting unlock when several grant one cell; unlocks_for_cell returns them all.
```

Group 1 reads bitmap ink only to place an unlock-added row’s anchor. The documented unlock shape has no `x:` (the authoring caveat on qsTea full’s ss03 x-height entry), so the row takes its anchor by convention from the stance’s base bitmap: entry x at the leftmost ink, exit x one past the rightmost. A base bitmap with no ink at the unlocked height raises a `SpecError`. `unjoined: safe` verification is exposed as `CellPlan.safety_checks`, which Group 3’s defect gates check.

### Group 2 — semantics (`settle`, `table`, `explain`)

The crate under `rebuild/kernel-rs/` implements this group. It carries over the prototype’s settlement and table semantics (Recon B’s promotion map), adds §6.2, and uses `ResolvedSpec` and `CellId` where the prototype had a hand-encoded spec and naming conventions. `rebuild/pipeline/kernel_exec.py` is the Python side of it: it builds the binary, writes the spec dump, runs the subcommands, and decodes their results into the pipeline’s types, and its module docstring maps it. Where each responsibility lives (crate files are under `rebuild/kernel-rs/src/`):

- **Settling one window** (the §6.1 steps: entry binding, the lookahead closure, refusals, the ranking stages, commitment, and the §6.2 extensions and contracts, most specific wins and never summed on one side): `engine.rs`, through `Engine::transition_trace`, which raises `E-UNACCEPTED-EXIT` when a committed exit has no refusal-aware acceptor at the next position. `cases.rs` serves it as the `settle-cases` subcommand. Python poses windows through `kernel_exec.settle_cases` (full traces), `settle_windows` (settled records only), and `settle_sequences` and `settle_codepoints` (whole texts, one position at a time), and `kernel_exec.trace_of` decodes a trace into `settle.TransitionTrace`.
- **Specificity** (§6.2, §15.5): `specificity.rs`, whose `outranks` is the only implementation and whose module doc gives the stratified evaluation.
- **Ligature formation and the §5.7 late-formation guard**: `guard.rs` is the only place the guard verdict is computed. `kernel_exec.guard_sweep` returns the verdict map, `settle.form_ligatures` forms the ligatures before settlement for the Python callers, and `options.rs` decides under which followers the table enumerates a pair unformed.
- **The table build**: `fixpoint.rs` (the worklist fixpoint over every reachable window), `fold.rs` and `rulefold.rs` (the rule fold, the rule order, and the table assertions, among them `fold::assert_outcome_partition`, the outcome-partition invariant of prototype follow-up 1), `certificate.rs` (one certificate per rule), `artifacts.rs` (the settlement and join TSVs and the windows enumeration), and `memo.rs` and `fanout.rs` (the configurations after `default` as deltas over its memo). `kernel_exec.build_table_files` runs one `build-tables` process over every settlement configuration, and `run_m1` calls it. `kernel_exec.build_tables(spec, features)` is the single-configuration form, which only tests call. `table.py` holds the data model (`DecisionTable`; `JoinTable`, one row per reachable adjacent cell pair with its join height or break, summed extension, and kern, 0 in M1), the artifact readers, and the digests, and its module docstring states which windows the table holds.
- **The vocabulary**: `settle.py` holds the types a window and its trace are stated in (`RightToken`, `LeftContext`, `Candidate`, `TransitionTrace`), boundary semantics, the tokenizer, and word position. They live there and not in `model.py`, whose frozen block has no window frames. `types.rs` is the crate’s counterpart.
- **Explain** (§6.3 item (a)): `explain.py`, run as `uv run python -m rebuild.pipeline.explain E665:E670:E665 --features ss03`. `explain(spec, codepoints, features) -> ExplainReport` settles through `kernel_exec.settle_sequences` and renders, per position, the full candidate table, every elimination attributed to its file and record, and the ranking stage that chose the winner. It accepts qs-names or hex codepoints, colon-separated.

The group’s design facts:

- **Reachability conditioned on the follower makes the fixpoint window-exact.** A settled left state is enqueued only against the `right1` that was the producing window’s `right2`. An entry refusal or unlock conditioned on the follower (qsTea’s half x-height entry refused before qsTea under ss03) makes other combinations contradictory, and a naive enumeration produces unreachable windows that raise `E-UNACCEPTED-EXIT`. Windows that formation makes impossible (an adjacent ligature pair left unformed) are excluded too.
- **ZWNJ-locked inputs enumerate under the locked copy’s name**, `model.locked_glyph_name` (`<raw>.noentry`), locked before settlement as in the prototype. This keeps each plain input’s boundary-left outcomes in one block, and the emitter, the raw-pipeline replay, and glyph minting all use the same name. In the settled output, `locked` is a `CellId.adjustments` token, which cell labels write out (`qsTea.half.ex-y5.locked`). The boundary lookahead class is `(uni200C, space, periodcentered)`: the namer dot is in it because it has no join surface, although it does not split a run for word position.
- **Table labels do not depend on the configuration.** Marker renaming and the fold’s no-conflict assertion are in `emit_gsub` (Group 3). Joint rows combine the realization ties the final tiebreak broke with the table-level comparison of the optimistic prospect against the settled follower.
- **Ranking-stage interpretation, fixed by tests:** `order:` (stage 4) applies across stances before the final tiebreak, so a non-joining preferred stance beats another stance’s baseline join with an equal join count. Cross-rune prefer conflicts at non-nested specificity raise `E-INCOMPARABLE` unless a `resolve` settles them, and same-rune ones `E-AMBIGUOUS`. Non-nested extend overlaps with equal demands are tolerated, with `ok:` normalized to its `[by, by]` default before demands are compared.
- **`fixtures.py`’s mini-spec must carry every record that fires inside the alphabet.** qsTea’s full-baseline-entry refusal is the example: without it ·Tea·Tea, ·Pea·Tea, and an entered ·It·Tea join, contradicting the authored YAML. A record may be left out only where it is vacuous over the alphabet.
- **Authored-data findings are asserted as authored, never quietly fixed.** `rebuild/test_settle.py` asserts them over the fixture spec on rows marked AUTHORED-DATA FINDING. One is qsMay’s declined exit mid-word, which renders with the fixture’s pulled-back unjoined binding; `qsMay.yaml` declares no unjoined binding. Another is qsIt’s baseline-exit refusal toward qsTea, qsRoe, and qsIt, which applies only to unentered cells, so an entered ·It joins a following ·It at the baseline where the old font breaks.

### Group 3 — realization (`geometry`, `defects`, `emit_gsub`, `emit_gpos`, `compile_font`, `readback`, `conform`, `baseline_subset`, `coretext_smoke`)

```python
# geometry.py
def realize(spec: ResolvedSpec, plan: CellPlan, adjustments: tuple[str, ...] = (), name: str | None = None) -> GlyphRecord: ...
    # resolution order per §3.2: explicit cells: binding > side bindings (stub/joined/unjoined) > base
    # bitmap; then extend/contract same-row connector arithmetic, trim, bind: substitutions with anchor
    # overrides; then anchors per convention (entry.x = min_ink_x_at_entry_y, exit.x = max_ink_x_at_exit_y+1,
    # x_off_convention exceptions honored). GlyphRecord = generated display name (≤63 bytes, hash overflow),
    # bitmap rows, y_offset, entry/exit anchors in pixels, entry_curs_only flag, advance.
def isolated_cell(spec: ResolvedSpec, rune_name: str) -> CellId: ...
    # the cell the raw cmap glyph renders: the default stance with no entry, and with the exit whose connector
    # ink is part of the base drawing (marked by an unjoined: binding to a named drawing; safe means the base
    # drawing already leaves no reaching ink there).
def display_name(spec: ResolvedSpec, cell: CellId) -> str: ...
    # the raw cmap glyph must be named exactly qsMay, so the isolated cell is named bare. A cell with no exit
    # where its stance's base drawing has one gets an ex-wd part so names stay unique. Names are never parsed.
def junction_gap(left: GlyphRecord, right: GlyphRecord, height: Height | int) -> int: ...   # the §9 gap arithmetic
def verify_unjoined_safe(record: GlyphRecord, side: str, height: Height | int) -> bool: ...

# defects.py
def run_gates(spec, tables_by_config, glyphs: Mapping[CellId, GlyphRecord], allow: frozenset[str] = frozenset()) -> DefectReport: ...
    # E-DANGLE (every reachable declined side), E-UNREALIZED (gap == 0 for every join row that joins),
    # E-ANCHOR (convention violations, checked only here: every realized GlyphRecord against the drawing it
    # ships, so qsPea's dip anchors are checked against the drawing §3.2 requires, and the selectable: false
    # rows against their stance's base drawing), off-anchor contact (overlay every reachable adjacency at
    # settled offset), extension band (ok:), dead policy. A defect whose signature is in allow (the reviewed
    # entries of rebuild/m1-contact-allow.yaml) is reported as blessed. Dead policy splits into (a) records
    # waiting on unmigrated letters, where every family the condition can match lacks a rune file (reported,
    # not failed), and (b) unused records, which wait on no unmigrated letter and still never fire; each must
    # be absent or carry a written explanation. The run's lists are written to
    # rebuild/out/m1/pipeline_summary.json as unused_records and waiting_on_unmigrated.
    # Errors fail; flags report. The module docstring gives the table shapes it reads (duck-typed, so a
    # (DecisionTable, JoinTable) pair or one object serves), the two coarse checks (the extension band per
    # junction against the union of the candidate bands on the pair's runes, and dead-policy use judged by
    # provenance citations), and which sides E-ANCHOR exempts.

# emit_gsub.py / emit_gpos.py
def emit_gsub(spec, tables_by_config: Mapping[frozenset[str], DecisionTable], glyphs=None, ss10_copies=None,
              namer_dot=("periodcentered", "periodcentered.lowered")) -> GsubPlan: ...
    # stage order (the module docstring gives each stage's reason): ss10 input substitution (every letter's
    # cmap glyph → its anchor-free .ss10 copy) → formation (the §5.7 guard's m1_formation_guarded first) →
    # ss markers (unconditional, per set; composite markers for the declared interactions, ss03+ss05 on
    # qsTea) → ZWNJ lock → ONE settlement lookup (per-family subtables) → namer-dot mini-calt (supplied
    # here because the senior_fea path has none).
    # glyphs (the realized inventory) supplies the glyph names the rules are checked against and the
    # namer-dot follower class; without it the namer-dot stage is left out. ss10_copies (raw cmap glyph name →
    # .ss10 copy name) feeds the ss10 input substitution; without it that stage is left out and the FEA says
    # so in a comment. namer_dot names the dot and lowered-dot pair.
    # The configuration fold: model.raw_rename_map maps each rune whose own unlocks the configuration's sets
    # touch to its marker copy (and <rune>.noentry to <marker>.noentry). _fold_rules renames each
    # configuration's rules through it before the exact-duplicate union, which is what makes the fold
    # conflict-free (a conflict raises EmitError). _ordered_settle_rules then orders each input's merged rules:
    # the crate's ZWNJ backtrack-slot guards first, then the other backtracked rules, then the rest, and within
    # each block, rules whose lookahead names a marker copy or uni200C before the other rules. That is sound
    # because marker substitution is unconditional, so a marker label and the bare label it replaces never
    # appear in the same stream, and it keeps the crate's guards-first order and each table's boundary-first
    # order, which a rune the ZWNJ lock leaves raw needs under HarfBuzz's ZWNJ skip. conform.absorb_replay_memo
    # reads the crate's window labels through the same map.
    # Asserted: no locked copy or ZWNJ lock output in any raw lookahead class; every named glyph exists; every
    # table rule of every configuration folds into exactly one emitted row (_assert_fold_sources); per-rule
    # provenance comments.
def emit_gpos(glyphs: Mapping[CellId, GlyphRecord], spec: ResolvedSpec | None = None) -> str: ...
    # one curs lookup per height that has anchors in the glyph set; the migrated runes declare rows at all
    # four heights (y6 is live via qsPea), so the M1 build emits four. NULL anchors for cross-height cells;
    # NULL/NULL coverage-only registrations for locked copies, which take spec because the glyph mapping
    # alone cannot supply them; pixels × 50 in the drawn frame with explicit advances.
def cursive_registrations(glyphs, spec=None) -> dict[int, dict[str, Registration]]: ...
    # the per-height anchor registry the curs lookups render, in font units: read-back's GPOS expectation.

# compile_font.py
def build_mini_font(glyphs, fea: str, out_path: Path) -> Path: ...
    # the prototype's recipe: legacy glyphs:-only dict (qs names suffixed .prop), empty glyph_families, the
    # prototype's metadata dict under the family name AbbotsMortonSpaceportM1 (metric parity with
    # glyph_data/metadata.yaml is left to integration), build_font(..., variant="senior", senior_fea=fea);
    # space and uni200C records are added when absent. pack_gsub repacks the settlement lookup's format-3
    # subtables into shared-ClassDef format-2 groups before the first save, and the settlement lookup uses
    # GSUB type 7 Extension offsets (useExtension).

# readback.py — the post-compile read-back stage
def verify_font(font_path: Path, plan: GsubPlan, cursive: Mapping[int, Mapping[str, Registration]]) -> dict: ...
    # re-parses the written bytes and compares them structurally with the emitters' own plan: every GSUB
    # stage's decompiled rules (the packed settlement lookup through pack_gsub.per_glyph_sequences),
    # FeatureList/ScriptList registration, cross-feature LookupList order, every lookupFlag, and the four
    # curs lookups' anchor records. A structural comparison with the plan that predicts no shaping. The GSUB
    # subtable-offset headroom is read off the same bytes and held to SUBTABLE_OFFSET_HEADROOM_FLOOR.
    # plan is the GsubPlan emit_gsub fills at emission with a structured copy of every stage (the ss10 input
    # map, the guarded and plain formation rows, the per-feature marker substitutions, the folded and ordered
    # settlement rules, the namer-dot stage, the calt stage list), so the comparison is with the same
    # in-memory data that produced the FEA text, never a re-derivation that could disagree. cursive is
    # emit_gpos.cursive_registrations. The scope is the back half: FEA text → feaLib → packing →
    # serialization → re-parsed bytes. The fold → FEA front half keeps its emission-time assertions and is
    # covered by conform.
    # run_m1 runs it immediately after build_mini_font and writes readback_summary.json beside the other
    # per-gate summaries; a divergence raises ReadbackError and fails the build.

# baseline_subset.py — streams rebuild/out/baseline-<config>.tsv.gz through
# rebuild/validation/rowmodel.open_table, keeps rows whose codepoints are all in the alphabet, and writes
# rebuild/out/m1/baseline-<config>.subset.tsv.gz in canonical row order.

# conform.py
def run_conformance(font_path, spec, configs=ACCEPTANCE_CONFIGS, glyphs=None, max_length=4, ...) -> ConformReport: ...
    # the per-edit sweep: every text from length 1 up to the conform maximum length (--conform-max-length, default 4)
    # over the alphabet, per acceptance configuration (the ss10 overlay at conform.OVERLAY_MAX_LENGTH); HarfBuzz
    # against settlement transition by transition (names via TTFont, never glyph_to_string); gap-0 pen
    # positions; split-buffer equivalence. It samples HarfBuzz application semantics and the sufficiency of
    # the window abstraction.
    # No coverage accounting; rule coverage (the §10 tier-3 obligation) is checked elsewhere. Read-back checks
    # per build that the font holds every planned rule and that its ZWNJ and space glyphs are inert. The
    # crate's fold fails the build on a rule that sits behind another and never wins a window. run_m1's
    # witness stage (run_m1.run_rule_witnesses) settles the certificate the crate builds from the table's row
    # chains for every rule (rebuild/kernel-rs/src/certificate.rs), which shows every settlement rule is
    # reachable; it runs on the tables the build just folded, so it never sees a stale enumeration.
    # emit_gsub._assert_fold_sources makes a certified table rule a certified shipped row.
    # `make conform-deep` (rebuild/tools/deep_sweep.py) runs the same sweep at maximum length 5+ on demand. It
    # falls due when the behavior classes emit_gsub.behavior_classes enumerates (it fails on any rule shape it
    # does not recognize), the font-compilation code, or the uharfbuzz version change, and its green record is
    # keyed on those three. Where the deep slots enumerate at class grain, a witnessed row stands for a fiber
    # of label windows the build has checked.

# oracle.py
def compare_against_baseline(spec, subset_tables_dir, alias_path, ledger_path, configs=ACCEPTANCE_CONFIGS, ...) -> BaselineReport: ...
    # the oracle gate over ligation, junctions, cell identity, and positions; procedure in §6 of this plan.
    # Ledger counts go to BaselineReport and divergence-audit.tsv; the run never rewrites the ledger YAML.

# coretext_smoke.py — keeps its own copy of the Swift harness in rebuild/pipeline/ and compiles it with swiftc
# when its binary is missing or older than the source, passes hex codepoints on argv, asserts that CoreText
# resolved the M1 font by PostScript name, and diffs GIDs and positions against HarfBuzz under every
# configuration in coretext_smoke.FEATURE_CONFIGURATIONS (the configurations of conform.ACCEPTANCE_CONFIGS).
# rebuild/pipeline/smoke_sequences_m1.txt is the sequence set: qsPea rows (·Pea·Pea y6 chain, ·May·Pea·It
# both-dipped cell, ·See-less en-y6 boundary rows), the migrated-family junctions, qsTea_qsOy windows, and
# namer-dot rows.
```

### §6.1/§6.2 semantics coverage — what the real records exercise (the durable record)

The old font’s ·Pea has ten stances whose only cross-rune mechanism is the `reverse_upgrade_from` chain of contextual substitutions. Settlement does not use it, and nothing in the rebuild replays it. An `E-INCOMPARABLE` or `E-AMBIGUOUS` that arises from real records fails the gate until the author settles it with the remedy §6.2 names for its kind of conflict. A `resolve` record settles a conflict between prefers on two runes, and the `E-INCOMPARABLE` message carries a paste-ready stub for it. Two prefers on one rune (`E-AMBIGUOUS`) and two extend or two contract records on one rune (`E-INCOMPARABLE`) take an edit to the records instead, because the engine consults resolves only where prefers on two runes collide. An `E-INCOMPARABLE` that a resolve raises itself, when two matching resolves pick differently or a matching resolve’s `pick:` admits no surviving candidate, takes an edit to the resolves.

Exercised by real records:

- **allowlist polarity**: qsPea’s x-height entry `from: [{family: qsMay}, {family: qsUtter}]`. The row’s own height is implied, so the scope names families only, and the ·Utter ligatures match through left-family transparency.
- a **left resolved-state condition** in policy: qsPea’s en-y6 baseline-exit refusal, `when: {left: {joined_at: y6}, …}`.
- a refusal whose `when:` uses a **predicate class plus an `except` carve-out**: that same refusal, toward can-enter-at-x-height minus a hand-listed carve-out.
- an **explicit `cells:` row with a per-cell anchor override**: qsOy’s opened loop, `{entry: x-height, exit: baseline, bitmap: open-on-the-left, exit_x: 5}`.
- a **side-binding anchor override**: qsMay’s `joined_x: 2`.
- **both stub polarities**: qsPea’s dips are ink present only when joined, and qsMay’s grounded loop has base-drawing ink that is removed when it joins.
- the **flagged oddity** `ink_y` (qsPea.half).
- the **y6 height** (·Pea·Pea, so all four curs lookups).
- the **`self:` condition** (qsIt’s exit extension when entered).
- **ss-gated extends** (qsMay toward qsTea under ss03).
- **unlocks in each capability set** (ss03, ss04, ss05): the ss03 and ss04 ones narrowed by a `when:` and the ss05 one with no context.
- **multi-set union composition with composite markers** (ss03+ss05 on qsTea).
- `pairings: only:` (qsIt) and `never:` (qsTea, qsMay).
- the predecessor's unjoined exit before the entryless ligature, and the §5.7 late-formation guard (`m1_formation_guarded` in `emit_gsub`).
- `bind:` and `trim:` at settlement level (qsOut, qsOut_qsTea, qsRoe).
- `resolve` in its against-a-named-record form (qsTea_qsOy).
- the ss10 isolated overlay.

Not exercised by real records: positive `word:` records, `split:`, `is: namer-dot` conditions, `stroke:` conditions in policy, `selectable: false` (only the fixture spec’s qsTea.half top entry uses it), the `resolve` tiebreak form (`at:`), and grouped resolves and the subsumption linter (none of these three is implemented). qsMay’s after-·Fee bound contract is in no rune: qsMay.yaml’s x-height entry row binds `pulled-back-stubless` for every enterer, so the contract could never be shown to fire. `rebuild/pipeline/fixtures.py` keeps that record as the synthetic example of a `bind:` contract, and `rebuild/test_defects.py` checks it as a record waiting on an unmigrated letter (the fixture spec has no qsFee rune), so its difference from `qsMay.yaml`’s contract list is intended. **§6.2 extensional specificity is fully implemented with its own regression tests; its two named design cases (the window of an x-height-exiting predecessor, ·It and ·They, and the qsJay contract-vs-extend overlap) run as synthetic analogs in `specificity.rs`’s tests, not on rune files.** This section is the only place that list is kept; the milestone gets no separate report.

Authoring rule from the prototype’s probed corrections (deviation 6): where the old YAML declares a record that the baseline shows never fires on a subset window (the plan’s example was qsIt’s entry extension after half-·Tea), author the record as the YAML has it. `E-UNREALIZED`’s gap arithmetic and the baseline comparison decide whether it fires, and any divergence goes into the ledger with the probe as evidence instead of into a silent spec edit.

The old font’s behavior outranks a literal reading of its YAML when the two disagree. ·May·Tea breaks while ·May·May joins, and the off-anchor contact gate rejects that join on its own, so qsMay’s baseline-exit refusals name ·Tea. Findings the gates do not contradict stay as authored and go in the ledger.

## 6. Acceptance and the divergence ledger

### Oracle-conformance procedure

1. `baseline_subset.py` filters every `rebuild/out/baseline-*.tsv.gz` (one streaming pass each, canonical row order kept) to the alphabet, caching the sub-tables under `rebuild/out/m1/`. The same pass checks that the ss06, ss07, and ss06+ss07 sub-tables are row-identical to default’s, so default’s run covers them, and writes the old-glyph-name list (`subset-names.json`) that the alias-completeness check reads.
2. For each acceptance configuration (`conform.ACCEPTANCE_CONFIGS`), every sub-table row is settled and compared **transition by transition**: (a) ligation, through clusters; (b) every junction’s classification (join height or break), taken on the new side from the settled junctions; (c) cell identity through `rebuild/m1-aliases.yaml` (hand-written: old compiled glyph name → `CellId`; `run_m1` stops before the oracle while any subset-row glyph name lacks an entry or a `pending` acknowledgment); (d) positions, kern-normalized per §2 of this plan. The oracle shapes every junction- and ligation-identical row with the new font and diffs drawn positions against the baseline as per-slot glyph origins plus the run’s total advance, because the two fonts can split a junction differently between the left glyph’s advance and the right glyph’s x_offset. `oracle_positions.KernEvaluator` evaluates the sidecar kerns read-only and adds each back. `uni200C` is default-ignorable, so the old font kerns across it, and the normalization’s kern partner skips ZWNJ slots. Rows whose matched cell-grain ledger class legitimately redraws ink are excluded and counted; `ink_identical: true` in the ledger marks the classes whose claim the position comparison checks.
3. The ledger matches through one classifier. `oracle.classify_divergence` assigns each divergent row a single class from its divergence tags (per-position alias-vs-settled cell differences plus junction gains and losses), and most ledger predicates are `classify(row) == id`, so those classes partition the rows by construction. Two predicates (`kern_out_of_scope`, `may_ligature_junction_loosened`) test rows directly, for rows the classifier leaves unassigned. A divergent row that matches two or more ledger entries fails the gate (overlapping predicates; `multi_matched` in `oracle_summary.json`). A divergent row that matches none is unmatched; unmatched rows wait on verdicts on the review corpus and do not fail the gate. Per-entry counts are written to `rebuild/out/m1/divergence-audit.tsv` and `oracle_summary.json`.
4. The font-side comparison, the conformance sweep of HarfBuzz against settlement, must be exact. The ledger applies only to the settlement-vs-baseline diff. A font-vs-settlement difference is a compiler defect by definition (§1).

### Ledger format — `rebuild/m1-divergences.yaml` (checked-in, human-reviewed)

One entry per divergence **class**, with a matching predicate, the observed count, example rows, and a required `why:`:

```yaml
- id: zwnj-word-initial-unification
  status: intended                # the ledger's header comment lists the status vocabulary
  match: {predicate: zwnj_word_initial_unification, configs: all}
    # predicate = a named matcher registered in oracle.py (small, reviewed functions over the row pair);
    # structured field predicates ({window: ..., junction_change: ...}) are also legal match shapes
  count: 0                        # copied from the run's audit and reviewed as a diff; the run never writes this file
  exemplars:
    - {config: default, codepoints: "200C:E650", baseline: "uni200C qsPea.noentry", new: "uni200C qsPea"}
  why: |
    Post-ZWNJ ≡ word-initial is definitional in the new model (§3.4), so the .noentry variants are deleted and the ZWNJ lock's locked copies replace them.
```

`rebuild/m1-divergences.yaml` holds the reviewed classes. The nine classes drafted with this plan are listed here, and the ledger’s `why:` fields cite them by number:

1. **zwnj-word-initial-unification** — the `.noentry` deletion. `oracle.classify_divergence` assigns it to rows with a `+locked` or `old-noentry` token; `boundary-window` takes every row that contains a ZWNJ first.
2. **space-vs-edge-guard-unification** — the same shape for the boundary-guard asymmetry. Not in the ledger: a class with no rows is not kept.
3. **unaccepted-exit-unjoined** — `E-UNACCEPTED-EXIT` and lookahead-closure semantics: ·It·It loses the old font’s harmless dangling ex-y5, and an entered ·It before an entryless follower settles with its exit unjoined (prototype divergences 1–2, generalized). In the ledger as **dangling-anchor-dropped**.
4. **same-junction-extension-non-summing** — the right junction of ·May·It·May matches that of ·Tea·It·May (prototype divergence 3).
5. **ss03-zwnj-leak-fixed** — `qsMay ZWNJ qsTea` under ss03 does not join (prototype divergence 4; cross-shaper finding 1). Not in the ledger: the baseline junction was already a break, and every such row contains a ZWNJ, so the `boundary-window` class covers it.
6. **post-marker-ligature-formation** — `qsMay qsTea qsOy` under ss03 and `ZWNJ qsTea qsOy` form the ligature (the ligature forms before the marker lookups; prototype divergence 5 and deviation 5).
7. **ss03-chain-join-gains** — ·It·May·Tea and ·Tea·May·Tea under ss03 gain the second join under window join count (prototype deviation 3).
8. **final-tiebreak-mismatches** — the remaining greedy-vs-old differences in unpinned windows per §15.4, the catch-all that must stay small and itemized; every member row is listed in the audit TSV and checked by eye. Implemented as the narrower **regrouped-chain** (rows with both a gain and a loss).
9. **kern-out-of-scope** — position-only differences that the position comparison marks kern-attributable (every mismatched slot follows a kerned pair or sits next to a ZWNJ). Its status is `triaged`, so its rows are never accepted automatically. It is expected to be near zero after kern normalization.

A divergence that matches none of these gets a new reviewed entry with a `why:`, a fix, or a verdict on the review corpus.

## 7. Gates

All must pass for M1 completion; each is a command or an assertion in the `rebuild/` tests or the build:

1. **Old-font byte identity** — `make all`; `shasum -a 256 site/AbbotsMortonSpaceportSansSenior-Regular.otf` == `3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35`. The rune files do not feed the old pipeline, so any change here is a hard stop.
2. **`make test` passes** (the old font’s suite).
3. **`make test-rebuild` passes** (the rebuild suite).
4. **Schema validation and lints pass** — schema validation of every rune file and the registry; the stance-ID regex; ductus parity; the `right.then` prohibition; predicate-class derivability.
5. **§9 hard E-gates on the subset** — `E-UNACCEPTED-EXIT`, `E-DANGLE`, `E-UNREALIZED` (gap 0 on every join row that joins), `E-ANCHOR`, and off-anchor contact over every reachable adjacency. A finding whose signature `rebuild/m1-contact-allow.yaml` lists is reported as blessed. `E-INCOMPARABLE` and `E-AMBIGUOUS` are expected to be absent; any occurrence fails the gate until the author settles it with the remedy §6.2 names for its kind of conflict: a `resolve` record for prefers on two runes, an edit to the records for two prefers, two extend records, or two contract records on one rune, and an edit to the resolves for a conflict that a resolve raises.
6. **Dead policy absent or explained** — records waiting on unmigrated letters are reported as a list; each unused record must carry a written explanation.
7. **Outcome-partition invariant and GSUB offset headroom** — the crate’s fold asserts the outcome partition on every configuration’s table; read-back fails the build when the GSUB subtable-offset headroom falls below `readback.SUBTABLE_OFFSET_HEADROOM_FLOOR`, recorded under `gsub_budget` in `readback_summary.json`.
8. **Oracle conformance** (§6 above) — no subset baseline row matches more than one ledger entry (`multi_matched == 0`), across every configuration in `conform.ACCEPTANCE_CONFIGS`; the coverage of ss06, ss07, and ss06+ss07 rests on the identity checked in step 1 of the procedure above. Unmatched rows are informational and wait on verdicts on the review corpus.
9. **Font conformance** — the per-edit sweep: an exhaustive HarfBuzz sweep to the conform maximum length (default 4) against settlement, exact (no ledger), with split-buffer equivalence and gap-0 pen positions. Rule coverage belongs to read-back, the crate’s fold-time first-match check, and the build’s witness stage over the crate’s rule certificates. Read-back also checks that the boundary glyphs are inert (no substituted position admits `uni200C` or `space`, zero advance, no outline). `make conform-deep` runs the same sweep at maximum length 5+ on demand. The CoreText smoke passes over the extended sequence set.
10. **Ductus parity and draft flags** — every stance names a motion; every drafted motion carries `# DRAFT — pending author sign-off`; the sign-off worklist is those markers in the rune files, found by grep.
11. **Formatting** — `make prettier`; `markdownlint-cli2` clean over new `.md` files.
12. **Repo hygiene** — changes to the old pipeline keep its output byte-identical (gate 1); new code stays under `rebuild/`, runes under `glyph_data/runes/`, scratch under `tmp/`.

## 8. Ductus drafting protocol

This follows the ductus conventions in `AGENTS.md`. The canonical ductus is at the family level, which in a rune file is the top-level `ductus:` map. A stance gets its own ductus only for a different pen motion. Several valid drawing orders are `-` bullets within one motion. Join constraints are never ductus; they are `pairings` and `unlocks` data, and an old ductus bullet that states one moves into that data. Ink that the old font adds or removes only at a join is a §4 binding on the same motion, not a motion of its own, or ductus parity would require a stance for it (the old ·Pea’s “dipping” motions are the example). A ligature rune carries its own ductus (§5.7). Drafting sources, in order: the old YAML’s ductus entries, the bitmaps, the Manual’s general Writing section (it has no per-letter stroke prose; Recon A §4), and `doc/core-idea.md`. The flag rule: **any motion whose prose is not copied byte for byte from the old YAML carries a trailing `# DRAFT — pending author sign-off` comment on its key line.** Prose copied byte for byte carries no flag, though moving join constraints out of it into data still needs the author’s sign-off. The rune files are the sign-off list; grep for `# DRAFT`. Typo fixes (“recieve”, a mid-sentence capital “Then”) count as edits and so as drafts.

## 9. The milestone’s record

There is no closing report. The milestone’s record is the commit history plus the runes’ `why:` fields. The durable design facts are in this plan, each in the section it concerns: the `rebuild/script.yaml` location deviation (§1), the module contracts as built and the semantics-coverage record (§5), the oracle procedure and the ledger classes (§6), and the ductus drafting protocol (§8). The unused-record and waiting-on-unmigrated lists are in `rebuild/out/m1/pipeline_summary.json`.
