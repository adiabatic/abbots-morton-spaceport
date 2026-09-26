# M1 plan: the first runes and the §14 module skeleton

Decisions for milestone M1. The evidence behind them was `rebuild/recon/m1-families.md` (Recon A), `rebuild/recon/m1-integration.md` (Recon B), `prototype/PLAN.md`, `prototype/REPORT.md`, and `rebuild/BASELINE-REPORT.md`. Those files and the `prototype/` directory are deleted, so this plan’s Recon A, Recon B, and REPORT citations resolve only in git history. The design doc (`doc/rebuild-design.md`) is binding, and a bare section reference points into it. Two prototype follow-ups are requirements of this plan: the outcome-partition property is a hard build invariant (REPORT follow-up 1), and the settlement lookup’s Extension promotion is watched by the GSUB offset-headroom check (follow-up 2).

Rules for M1 work:

- New code goes under `rebuild/`.
- The old pipeline’s Senior Sans output stays byte-identical (SHA-256 `3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35`).
- Python runs through `uv run`, and broad test runs use `-n auto --dist worksteal`.
- Prose uses American English and never abbreviates “isolation” or “isolated”. Comments and docstrings are not hard-wrapped.
- YAML formatting follows `AGENTS.md` and `tools/reflow_yaml.py`.

## 1. Locations

**Rune files: `glyph_data/runes/`**, the address §2 gives them. The old pipeline finds YAML only through the non-recursive `path.glob("*.yaml")` in `tools/build_font.py`’s `load_glyph_data`, so the old font build never reads files in `glyph_data/runes/`. Each migrated family has one file named for its rune; a ligature’s file is named for its sequence (`qsTea_qsOy.yaml`).

**Registry: `rebuild/script.yaml`, a recorded deviation from §2 until cutover.** A `glyph_data/script.yaml` would break the old build: `load_glyph_data` reads any document without one of its `_STRUCTURAL_KEYS` as a Senior kerning rule, and `generate_kern_fea` then fails with a `KeyError` on it. The registry stays under `rebuild/` and moves to `glyph_data/script.yaml` at cutover. `spec_load.load_spec` takes the registry path as an argument, so the move changes `spec_load.DEFAULT_REGISTRY_PATH` and the few other places that name the path (`git grep '"script.yaml"'` lists them).

**Schemas: `rebuild/schema/rune.schema.json` and `rebuild/schema/script.schema.json`** (JSON Schema 2020-12). `spec_load` validates every rune file and the registry against them at load time (see “The spec front end” below). `.vscode/settings.json` maps both schemas to their files for editor validation.

**Modules: `rebuild/pipeline/` as a package**, named after §14:

```text
rebuild/pipeline/
  __init__.py
  model.py            shared frozen dataclasses that all three groups below code against; the kernel crate lists their fields by hand
  spec_load.py        YAML → ResolvedSpec; schema validation; lints (naming, ductus parity, right.then, dead reference)
  surface.py          cell enumeration; binding resolution; pairings/unlocks/scopes → CellPlan
  settle.py           the §6.1 settlement vocabulary: tokens, boundary cells, guarded formation; the settlement function itself is in the kernel crate
  table.py            the decision- and treaty-table data model and the readers for the crate's table artifacts
  geometry.py         per-cell bitmap/anchor realization; stubs; bindings; extensions; gap arithmetic
  defects.py          E-DANGLE, E-UNREALIZED, E-ANCHOR, off-anchor contact, dead policy
  emit_gsub.py        the staged GSUB (its module docstring gives the stage order)
  emit_gpos.py        per-height curs lookups
  compile_font.py     mini-font build via build_font(senior_fea=...) and the pack_gsub repack
  readback.py         post-compile read-back: the written font re-parsed and checked against the plan, GSUB offset headroom included
  conform.py          HarfBuzz sweep against settlement, the per-row baseline comparison and its memoized walk
  oracle.py           baseline-oracle driver, divergence classifier, ledger matching (outside the tables' stamp)
  oracle_positions.py the position channel: kern normalization, the drift diff, the served-position codec and verifier, the sidecar evaluator, the shaper factory (outside the tables' stamp and the row stamp)
  explain.py          the §6.3 item (a) CLI (python -m rebuild.pipeline.explain)
  baseline_subset.py  streaming filter of rebuild/out/baseline-*.tsv.gz to the migrated alphabet; refilters when the alphabet or the sources change
  coretext_smoke.py   CoreText-vs-HarfBuzz comparison over the extended sequence set
```

The package holds more modules than this skeleton, among them `kernel_exec.py` (the crate driver), `run_m1.py`, `witness.py`, `pack_gsub.py`, `manual_pins.py`, and `fingerprint.py`. Each module docstring states its role.

Tests are `rebuild/test_<module>.py`. Pytest’s `testpaths` excludes `rebuild/`, so `make test-rebuild` runs them. The build writes its artifacts to `rebuild/out/m1/` (gitignored with `rebuild/out/`): `M1.otf`, `M1.fea`, `settlement-<config>.tsv`, `treaties-<config>.tsv`, the per-gate summaries (`conform_summary.json`, `readback_summary.json`, `oracle_summary.json`, and others), `divergence-audit.tsv`, and the filtered baseline sub-tables. The divergence ledger `rebuild/m1-divergences.yaml` and the alias map `rebuild/m1-aliases.yaml` are checked-in, human-reviewed source files. `make prettier` runs black over the repository, `rebuild/` included (line length 110). `rebuild/REVIEW-PLAN.md` specifies the review surface that reads the divergence ledger.

## 2. M1 scope

**The first batch was qsPea, qsTea, qsMay, qsIt, qsOy as a fully modeled fifth rune, and the qsTea_qsOy ligature.** Each later batch adds one letter and any ligatures it completes. `rebuild/pipeline/baseline_subset.M1_ALPHABET` lists the migrated alphabet and `glyph_data/runes/` holds the rune files.

Why the first batch took this shape: Recon A found that the four families qsPea, qsTea, qsMay, and qsIt are formation-closed with no ligatures. The qsTea_qsOy ligature was added anyway, because the predecessor withdrawal before an entryless ligature (§5.7) tests the architecture more than any other available behavior, and the prototype had already shown that it works. Its trailing component qsOy is outside the four. The plan chose to bring qsOy’s rune file in as a full fifth rune instead of restricting the conformance alphabet to “qsOy only immediately after qsTea”. qsOy is small (a bare form, one x-height-entry stance, and its locked twin). Modeling it fully keeps the conformance gate total over the alphabet with no exceptions, where the restriction would have needed new code and left a documented gap. It also adds baseline windows that must conform once qsOy is typeable: `·May ~x~ ·Oy`, `·Pea | ·Oy`, and `·Tea+Oy`, all verified against the baseline. The prototype modeled qsOy as inert (its deviation 4). M1 models it fully, so qsOy’s real joins are conformance obligations. qsOut_qsTea was left out of the first batch because its interesting case, the after-·See `bind:` shape, needs qsSee, and without qsOut in the alphabet it could never form.

**Conformance alphabet:** the three boundary tokens (`0x0020` space, `0x00B7` namer dot, `0x200C` ZWNJ) plus one code point per migrated family. `baseline_subset.M1_ALPHABET` is the list. The alphabet is kept formation-closed: every ligature whose components are both in the alphabet has a rune file.

**Configurations.** `baseline_subset` filters every `rebuild/out/baseline-*.tsv.gz`, but the acceptance gate runs only on the configurations that can affect the migrated letters; `conform.ACCEPTANCE_CONFIGS` lists them. The reason for each: `ss03` (half-·Tea entry widening, qsMay’s ss03-gated exit extension toward qsTea, qsTea_qsOy formation staging), `ss04` (qsIt’s baseline-pairing unlock), `ss05` (·Tea joining at the baseline on both sides after ·Et), the declared combination `ss03+ss05` (multi-set union semantics and composite markers on qsTea), and `ss10` (the isolated-forms overlay, which touches every rune; `conform.OVERLAY_CONFIGS`). `ss06`, `ss07`, and `ss06+ss07` change only unmigrated families. Whenever `baseline_subset.refresh` refilters, it checks that their sub-tables (`DEFAULT_COVERED_CONFIGS`) are row-identical to `default`’s, so the default run covers them. The check runs at refilter time because only a refilter can change the answer. A configuration that diverges is never stamped fresh, so its `SubsetIdentityError` repeats on every run until that configuration moves into `conform.ACCEPTANCE_CONFIGS`.

**Kerning is outside M1 settlement.** The sidecar kerning records for the migrated families move over through §12 later, and the mini-font emits no kerning. The oracle’s position comparison therefore evaluates `glyph_data/senior_quikscript_kerning.yaml` read-only over the migrated pairs and adds each expected kern back before diffing. Any remaining position divergence is real. Position-only divergences that the position channel attributes to a kern or a ZWNJ adjacency match the `kern-channel-out-of-scope` ledger entry, whose status `triaged` means they are never accepted automatically (§12 keeps ZWNJ kerns as a proven pattern).

**The namer dot is in scope as a token.** M1’s emitter supplies its own dot-lowering stage, because `_namer_dot_calt_fea` does nothing on the `senior_fea` path (Recon B), and the glyph set includes `periodcentered` and `periodcentered.lowered`. No rune record conditions on `is: namer-dot`, so that condition value is registered but unexercised.

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
        x-height: {x: 5, stroke: horizontal, withdrawal: safe}
          # `withdrawal:` is the drawing used when this side declines a join mid-word. `safe` is allowed only when the build verifies there is no reaching ink (qsIt's bar); qsMay's own x-height exit declares neither. qsPea.half's exit row carries a flagged oddity instead:
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
          # qsTea.full carries: {pairing: {entry: baseline, exit: baseline}, feature: ss05, when: {left: {family: qsEt}}}
          # qsTea.half carries: {entry: x-height, feature: ss03, when: {left: {family: [...widened ss03 scope...]}}}
          # qsIt carries one ss04 row with no context: {pairing: {entry: baseline, exit: baseline}, feature: ss04}
      require: []                  # for a stance that exists only when joined; qsFee.reversed-loop is the example (require: [entry])
  grounded-loop:
    motion: grounded-loop
    bitmap: ["..."]
    y_offset: -3
    surface:
      entries:
        x-height: {x: 2, stroke: horizontal, stub: {cols: [3], inks_when: withdrawn}}    # the other stub polarity: ink in the base drawing that is removed when the side joins
      exits:
        baseline: {x: 4, toward: [{family: qsDay}, {family: qsSee}]}   # `toward:` is the exit-side allowlist (code-point order)
policy:
  order: [loop, grounded-loop]     # stance preference; stances left out follow in declaration order
  refuse:
    - {exit: baseline, when: {right: {family: [qsDay, qsThaw, qsZoo, qsYe, qsHe, qsNo, qsRoe, qsIt, qsEat, qsUtter, qsOoze]}}, why: These never receive ·May's grounded exit.}
    - {exit: baseline, when: {left: {family: qsRoe}, right: {family: qsEt}}}    # conditions on both sides when needed
      # refuse may not use right.then, because a refusal must be decidable one position to the left; the schema and a spec_load lint both enforce this
  prefer: []                       # yielding by default (cell: / over: / when:); mode: absolute outranks join count and needs a why:
  extend:
    - {exit: x-height, by: 1, ok: [1, 1], when: {right: {family: [qsDay, qsFee, qsJai, qsJay, qsRoe, qsIt]}}}
    - {exit: x-height, by: 1, when: {right: {family: qsTea}, feature: ss03}}    # the ss03-gated reach
    - {entry: baseline, by: 1, when: {left: {family: [qsPea, qsTea, qsYe, qsHe, qsIt]}}}
      # an entry-side extend, shown for its shape only (qsMay.yaml has none)
      # a record that grants or extends an entry at height H already fixes the seam at H, so the left scope never repeats it as `joined_at: H`.
      # qsIt's self: condition (the old font's extend_exit_when_entered):
      #   {exit: baseline, by: 1, when: {self: {entry: live}}}
      # side and height are required; `stance:` only when more than one stance offers that side and height (none of the three above needs it); records on the same side never sum; the most specific record wins (§6.2)
  contract:
    - {stance: loop, entry: x-height, bind: pulled-back-stubless, when: {left: {family: qsFee}}, why: ·Fee's long reach-over absorbs the baseline stub; the redraw spans rows, so it is a bound shape, not arithmetic.}
      # `bind:` substitutes a hand-drawn alternative for same-row arithmetic; `trim: N` blanks ink on the receiving side instead. `stance: loop` is needed here because both ·May stances offer an x-height entry. This record exists only in `rebuild/pipeline/fixtures.py`: qsMay.yaml's x-height entry row binds `pulled-back-stubless` for every enterer, so the rune needs no such contract (§5).
  resolve: []                      # where an E-INCOMPARABLE/E-AMBIGUOUS is decided, with migrated: provenance
  groups: {}                       # rune-local sets: {union: [...], minus: [...]} over family literals, traits, classes
```

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
  ss05: {kind: capability, description: "·Tea both-baseline after ·Et"}
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

The `families` registry lets the dead-policy gate tell a **deferred-partner record**, whose condition names only families that have no rune file yet, from dead policy. The gate lists deferred-partner records and does not fail on them.

## 4. The JSON schema

Files: `rebuild/schema/rune.schema.json` and `rebuild/schema/script.schema.json` (draft 2020-12). `spec_load` validates against them. What the schema enforces:

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
    seam: Height | None                        # the committed seam toward the next position
    extension: int                             # summed connector pixels this side carries on this seam

@dataclass(frozen=True)
class ResolvedSpec:                            # spec_load's output; the input to everything else
    runes: Mapping[str, Rune]                  # only the modeled runes (ligature files included)
    registry: ScriptRegistry                   # heights, boundary tokens, features + interactions, predicate classes (membership resolved), full family-name registry
    # Rune carries ductus, stances (with Surface: entry/exit rows incl. scopes/bindings/oddities, pairings,
    # cells, unlocks, require) and Policy (order, refuse/prefer/extend/contract/resolve, groups), all parsed
    # and scope-expanded but not yet geometry-resolved.
```

### Group 1 — the spec front end (`spec_load`, `surface`, the schemas)

```python
# spec_load.py
def load_spec(runes_dir: Path, registry_path: Path, schema_dir: Path) -> ResolvedSpec: ...
    # schema validation per file; then Python lints: stance-ID regex, ductus parity (every stance names a
    # motion, every motion has a stance), refuse right.then rejection, the then-chain depth cap, family
    # references resolved against the registry, predicate-class evaluation, duplicate rune-local-group flag.
    # Raises SpecError(file, path, message); collects all errors before raising.

# surface.py
def enumerate_cells(spec: ResolvedSpec, rune: str, features: frozenset[str]) -> tuple[CellId, ...]: ...
    # declared rows ∪ {none} per side, filtered by pairings (never/only), require, unlocks (an unlock's feature
    # and when: narrowing apply at settlement, so unlock cells are returned tagged with their unlock record).
def resolve_cell(spec: ResolvedSpec, cell: CellId) -> CellPlan: ...
    # binding resolution: which named bitmap the cell uses (explicit cells: row > side bindings
    # (stub/joined/withdrawal) > base), per-cell anchor x values (joined_x/entry_x/exit_x overrides), the
    # flagged oddities (ink_y, selectable), and the E-DANGLE obligation when a declined side has no
    # withdrawal binding and is not verified safe (geometry does the verification).
```

Group 1 reads bitmap ink only to place an unlock-added row’s anchor by convention. `withdrawal: safe` verification is exposed as `CellPlan.safety_checks`, which Group 3’s defect gates check.

### Group 2 — semantics (`settle`, `table`, `explain`)

The crate under `rebuild/kernel-rs/` implements this group: settlement, specificity, the fixpoint enumeration, and the table fold. It carries over the prototype’s settlement and table semantics (Recon B’s promotion map), adds §6.2, and uses `ResolvedSpec` and `CellId` where the prototype had a hand-encoded spec and naming conventions. The Python side keeps the vocabulary (`settle.py`), the table data model and artifact readers (`table.py`), the crate driver (`kernel_exec.py`), and `explain.py`. The contract below is the plan’s; the “Semantics” design facts say where each piece lives.

```python
# settlement: the crate; Python callers use kernel_exec.settle_cases / settle_windows / settle_sequences
def transition(spec: ResolvedSpec, left: LeftContext, token: RightToken,
               right1: RightToken | None, right2: RightToken | None,
               features: frozenset[str]) -> Settled: ...
def settle(spec: ResolvedSpec, codepoints: Sequence[int], features: frozenset[str]) -> list[Settled]: ...
    # formation first (unconditional type-4 over the registry's ligature sequences), then §6.1 steps 1–5 per
    # position. Implements: entry binding; lookahead closure; refusals (both sides, all grains, except
    # carve-outs); ranking = absolute prefers (most-specific first) → window join-count → yielding prefers →
    # order: → structural floor (realize left seam, lower height, row declaration order, none last) → weak
    # lead preference; commitment. Boundary semantics: space/ZWNJ split runs, namer-dot does not; word
    # position derived. Extensions and contracts applied per §6.2 most-specific-wins, never summed same-side.

# specificity (rebuild/kernel-rs/src/specificity.rs; §15.5):
def outranks(spec: ResolvedSpec, a: PolicyRecord, b: PolicyRecord) -> Ordering: ...
    # extensional: every constrained axis expands to its concrete match set over the finite registry; A
    # outranks B iff subset on every axis B constrains, one strict. Non-nested overlap with conflicting
    # demands → E-INCOMPARABLE. Stratified evaluation (capability, then policy).

# table build: kernel_exec.build_tables drives the crate's fixpoint and fold; table.py holds the data model
def build_tables(spec: ResolvedSpec, features: frozenset[str]) -> tuple[DecisionTable, TreatyTable]: ...
class DecisionTable:
    rules: tuple[Rule, ...]                    # ordered: boundary rows with explicit uni200C first, more lookahead slots before fewer, identity rows omitted, slot-dropped fallback last
    def reachable_cells(self) -> frozenset[CellId]: ...
    def joint_rows(self) -> frozenset[int]     # optimistic-prospect-vs-settled divergence + floor-broken realization ties
    def write_tsv(self, path: Path) -> None    # settlement-<config>.tsv with provenance pointers (§8 artifact)
    # the outcome partition (prototype follow-up 1, a hard build failure) is asserted by the crate's fold::assert_outcome_partition
    # E-STRANDED (a committed exit with no refusal-aware acceptor at the next position) is raised by the crate's settlement
class TreatyTable:
    rows: tuple[TreatyRow, ...]                # one per reachable adjacent cell pair: join height or break, summed extension, kern (0 in M1)
    def write_tsv(self, path: Path) -> None

# explain.py — the §6.3 item (a) CLI
# uv run python -m rebuild.pipeline.explain E665:E670:E665 --features ss03
def explain(spec: ResolvedSpec, codepoints: Sequence[int], features: frozenset[str]) -> ExplainReport: ...
    # per position: the full candidate table; every elimination attributed to provenance (file + record);
    # the rank comparison that chose the winner (which lexicographic stage decided, and between which two
    # candidates); rendered as aligned text. Accepts qs-names or hex codepoints, colon-separated.
```

### Group 3 — realization (`geometry`, `defects`, `emit_gsub`, `emit_gpos`, `compile_font`, `readback`, `conform`, `baseline_subset`, `coretext_smoke`)

```python
# geometry.py
def realize(spec: ResolvedSpec, plan: CellPlan, adjustments: tuple[str, ...]) -> GlyphRecord: ...
    # resolution order per §3.2: explicit cells: binding > side bindings (stub/joined/withdrawal) > base
    # bitmap; then extend/contract same-row connector arithmetic, trim, bind: substitutions with anchor
    # overrides; then anchors per convention (entry.x = min_ink_x_at_entry_y, exit.x = max_ink_x_at_exit_y+1,
    # x_off_convention exceptions honored). GlyphRecord = generated display name (≤63 bytes, hash overflow),
    # bitmap rows, y_offset, entry/exit anchors in pixels, entry_curs_only flag, advance.
def seam_gap(left: GlyphRecord, right: GlyphRecord, height: Height) -> int: ...   # the §9 gap arithmetic
def verify_withdrawal_safe(record: GlyphRecord, side: str, height: Height) -> bool: ...

# defects.py
def run_gates(spec, tables_by_config, glyphs: Mapping[CellId, GlyphRecord]) -> DefectReport: ...
    # E-DANGLE (every reachable declined side), E-UNREALIZED (gap == 0 for every treaty join row),
    # E-ANCHOR (convention drift), off-anchor contact (overlay every reachable adjacency at settled offset),
    # extension band (ok:), dead policy. Dead policy splits into (a) deferred-partner records, where every
    # family the condition can match lacks a rune file (reported, not failed), and (b) records dead within
    # the modeled alphabet, which must be absent or carry a written explanation. The run's lists are
    # written to rebuild/out/m1/pipeline_summary.json as dead_in_alphabet and deferred_partner.
    # Errors fail; flags report.

# emit_gsub.py / emit_gpos.py
def emit_gsub(spec, tables_by_config: Mapping[frozenset[str], DecisionTable]) -> GsubPlan: ...
    # stage order: ss10 pre-empt (every letter's cmap glyph → its anchor-free .ss10 twin) → formation →
    # ss markers (unconditional, per set; composite markers for the declared interactions, ss03+ss05 on
    # qsTea) → ZWNJ chokepoint → ONE settlement lookup (per-family subtables) → namer-dot mini-calt (supplied
    # here because the senior_fea path has none). Asserted: no locked twin or chokepoint output in any raw
    # lookahead class; every named glyph exists; per-rule provenance comments.
def emit_gpos(glyphs: Mapping[CellId, GlyphRecord]) -> str: ...
    # four per-height curs lookups (y6 is live via qsPea); NULL anchors for cross-height cells; NULL/NULL
    # parity registrations for locked twins; pixels × 50 in the drawn frame with explicit advances.

# compile_font.py
def build_mini_font(glyphs, fea: str, out_path: Path) -> Path: ...
    # the prototype's recipe: legacy glyphs:-only dict (qs names suffixed .prop), empty glyph_families,
    # metadata, build_font(..., variant="senior", senior_fea=fea); pack_gsub repacks the settlement lookup
    # before the first save.

# readback.py — the post-compile read-back stage (a signature extension recorded below)
def verify_font(font_path: Path, plan: GsubPlan, cursive) -> dict: ...
    # re-parses the written bytes and compares them structurally with the emitters' own plan: every GSUB
    # stage's decompiled rules (the packed settlement lookup through pack_gsub.per_glyph_sequences),
    # FeatureList/ScriptList registration, cross-feature LookupList order, every lookupFlag, and the four
    # curs lookups' anchor records. A transcription round-trip that predicts no shaping. The GSUB
    # subtable-offset headroom is read off the same bytes and held to SUBTABLE_OFFSET_HEADROOM_FLOOR.
    # Divergences are written to readback_summary.json and fail the build.

# baseline_subset.py — streams rebuild/out/baseline-<config>.tsv.gz through
# rebuild/validation/rowmodel.open_table, keeps rows whose codepoints are all in the alphabet, and writes
# rebuild/out/m1/baseline-<config>.subset.tsv.gz in canonical row order.

# conform.py
def run_conformance(font_path, spec, configs) -> ConformReport: ...
    # the per-edit belt: every text from length 1 up to the conform horizon (--conform-horizon, default 4)
    # over the alphabet, per acceptance configuration (the ss10 overlay at conform.OVERLAY_HORIZON); HarfBuzz
    # against settlement transition by transition (names via TTFont, never glyph_to_string); gap-0 pen
    # positions; split-buffer equivalence. No coverage accounting: read-back checks per build that the font
    # holds every planned rule and that its ZWNJ and space glyphs are inert, the crate's fold fails on a
    # rule no replayed row first-matches, and run_m1's witness stage settles the certificate the crate
    # writes beside every rule.
    # `make conform-deep` (rebuild/tools/deep_sweep.py) runs the same sweep at horizon 5+ on demand; its
    # green record is keyed on emit_gsub.behavior_classes, the compilation code, and uharfbuzz.

# oracle.py
def compare_against_baseline(spec, subset_tables_dir, alias_path, ledger_path) -> BaselineReport: ...
    # the oracle gate; procedure in §6 of this plan.

# coretext_smoke.py — compiles the Swift harness with swiftc when its binary is missing or older than the
# source, passes hex codepoints on argv, asserts that CoreText resolved the M1 font by PostScript name, and
# diffs GIDs and positions against HarfBuzz; the sequence set adds qsPea rows
# (·Pea·Pea y6 chain, ·May·Pea·It both-dipped cell, ·See-less en-y6 boundary rows), the migrated-family
# seams, ligature windows, and every ss-marker configuration; rebuild/pipeline/smoke_sequences_m1.txt
# is the set.
```

### §6.1/§6.2 semantics coverage — what the real records exercise (the durable record)

The old font’s ·Pea has ten stances whose only cross-rune mechanism is the `reverse_upgrade_from` chain of contextual substitutions. Settlement does not use it, and nothing in the rebuild replays it. An `E-INCOMPARABLE` or `E-AMBIGUOUS` that arises from real records fails the gate until a `resolve` record decides it.

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
- **unlocks in each capability set** (ss03, ss04, ss05): two narrowed by a `when:` and the ss04 one with no context.
- **multi-set union composition with composite markers** (ss03+ss05 on qsTea).
- `pairings: only:` (qsIt) and `never:` (qsTea, qsMay).
- predecessor withdrawal before the entryless ligature, and the §5.7 late-formation guard (`m1_formation_guarded` in `emit_gsub`).
- `bind:` and `trim:` at settlement level (qsOut, qsOut_qsTea, qsRoe).
- `resolve` in its against-a-named-record form (qsTea_qsOy).
- the ss10 isolated overlay.

Not exercised by real records: positive `word:` records, `split:`, `is: namer-dot` conditions, `stroke:` conditions in policy, `selectable: false` (only the fixture spec’s qsTea.half top entry uses it), the `resolve` floor form (`at:`), and case-group promotion and the subsumption linter (none of these three is implemented). qsMay’s after-·Fee bound contract is in no rune: qsMay.yaml’s x-height entry row binds `pulled-back-stubless` for every enterer, so the contract could never be shown to fire. `rebuild/pipeline/fixtures.py` keeps that record as the synthetic example of a `bind:` contract, and geometry’s tests use it, so its difference from `qsMay.yaml`’s contract list is intended. **§6.2 extensional specificity is fully implemented with its own regression tests; its two named design cases (the decline-discriminator window and the qsJay contract-vs-extend overlap) run as synthetic analogs in `specificity.rs`’s tests, not on rune files.** This section is the only place that list is kept; the milestone gets no separate report.

Authoring rule from the prototype’s probed corrections (deviation 6): where the old YAML declares a record that the baseline shows never fires on a subset window (the plan’s example was qsIt’s entry extension after half-·Tea), author the record as the YAML has it. `E-UNREALIZED`’s gap arithmetic and the baseline comparison decide whether it fires, and any divergence goes into the ledger with the probe as evidence instead of into a silent spec edit.

## 6. Acceptance and the divergence ledger

### Oracle-conformance procedure

1. `baseline_subset.py` filters every `rebuild/out/baseline-*.tsv.gz` (one streaming pass each, canonical row order kept) to the alphabet, caching the sub-tables under `rebuild/out/m1/`. The same pass checks that the ss06, ss07, and ss06+ss07 sub-tables are row-identical to default’s, so default’s run covers them, and writes the old-glyph-name list (`subset-names.json`) that the alias-completeness check reads.
2. For each acceptance configuration (`conform.ACCEPTANCE_CONFIGS`), every sub-table row is settled and compared **transition by transition**: (a) ligation, through clusters; (b) every seam’s classification (join height or break), taken on the new side from the settled seams; (c) cell identity through `rebuild/m1-aliases.yaml` (hand-written: old compiled glyph name → `CellId`; `run_m1` stops before the oracle while any subset-row glyph name lacks an entry or a `pending` acknowledgment); (d) positions, kern-normalized per §2 of this plan (sidecar kerns evaluated read-only and added back).
3. A divergent row that matches two or more ledger entries fails the gate (overlapping predicates; `multi_matched` in `oracle_summary.json`). A divergent row that matches none is unmatched; unmatched rows wait on verdicts on the review surface and do not fail the gate. Per-entry counts are written to `rebuild/out/m1/divergence-audit.tsv`.
4. The font-side comparison, the conformance sweep of HarfBuzz against settlement, must be exact. The ledger applies only to the settlement-vs-baseline diff. A font-vs-settlement difference is a compiler defect by definition (§1).

### Ledger format — `rebuild/m1-divergences.yaml` (committed-shape, human-reviewed)

One entry per divergence **class**, with a matching predicate, the observed count, example rows, and a required `why:`:

```yaml
- id: zwnj-word-initial-unification
  status: intended                # the ledger's header comment lists the status vocabulary
  match: {predicate: zwnj_word_initial_unification, configs: all}
    # predicate = a named matcher registered in oracle.py (small, reviewed functions over the row pair);
    # structured field predicates ({window: ..., seam_change: ...}) are also legal match shapes
  count: 0                        # copied from the run's audit and reviewed as a diff; the run never writes this file
  exemplars:
    - {config: default, codepoints: "200C:E650", baseline: "uni200C qsPea.noentry", new: "uni200C qsPea"}
  why: |
    Post-ZWNJ ≡ word-initial is definitional in the new model (§3.4), so the .noentry variants are deleted and the chokepoint's locked twins replace them.
```

`rebuild/m1-divergences.yaml` holds the reviewed classes. The nine classes drafted with this plan are listed here, and the ledger’s `why:` fields cite them by number:

1. **zwnj-word-initial-unification** — the `.noentry` deletion. `oracle.classify_divergence` assigns it to rows with a `+locked` or `old-noentry` token; `boundary-echo` takes every row that contains a ZWNJ first.
2. **space-vs-edge-guard-unification** — the same shape for the boundary-guard asymmetry. Not in the ledger: a class with no rows is not kept.
3. **stranded-exit-withdrawal** — `E-STRANDED` and lookahead-closure semantics: ·It·It loses the old font’s harmless dangling ex-y5, and an entered ·It before an entryless follower settles with its exit withdrawn (prototype divergences 1–2, generalized). In the ledger as **dangling-anchor-dropped**.
4. **same-seam-extension-non-summing** — the right seam of ·May·It·May matches that of ·Tea·It·May (prototype divergence 3).
5. **ss03-zwnj-leak-fixed** — `qsMay ZWNJ qsTea` under ss03 does not join (prototype divergence 4; cross-shaper finding 1). Not in the ledger: the baseline seam was already a break, and every such row contains a ZWNJ, so the `boundary-echo` class covers it.
6. **marker-staging-ligature-formation** — `qsMay qsTea qsOy` under ss03 and `ZWNJ qsTea qsOy` form the ligature (markers staged after formation; prototype divergence 5 and deviation 5).
7. **ss03-chain-join-gains** — ·It·May·Tea and ·Tea·May·Tea under ss03 gain the second join under window join count (prototype deviation 3).
8. **structural-floor-drift** — the remaining greedy-vs-old don’t-care differences per §15.4, the catch-all that must stay small and itemized; every member row is listed in the audit TSV and checked by eye. Implemented as the narrower **regrouping-floor-drift** (rows with both a gain and a loss).
9. **kern-channel-out-of-scope** — position-only differences that the position channel marks kern-attributable (every drifted slot follows a kerned pair or sits next to a ZWNJ). Its status is `triaged`, so its rows are never accepted automatically. It is expected to be near zero after kern normalization.

A divergence that matches none of these gets a new reviewed entry with a `why:`, a fix, or a verdict on the review surface.

## 7. Gates

All must pass for M1 completion; each is a command or an assertion in the `rebuild/` tests or the build:

1. **Old-font byte identity** — `make all`; `shasum -a 256 site/AbbotsMortonSpaceportSansSenior-Regular.otf` == `3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35`. The rune files do not feed the old pipeline, so any change here is a hard stop.
2. **`make test` passes** (the old font’s suite).
3. **`make test-rebuild` passes** (the rebuild suite).
4. **Schema validation and lints pass** — schema validation of every rune file and the registry; the stance-ID regex; ductus parity; the `right.then` prohibition; predicate-class derivability.
5. **§9 hard E-gates on the subset** — `E-STRANDED`, `E-DANGLE`, `E-UNREALIZED` (gap 0 on every treaty join row), `E-ANCHOR`, and off-anchor contact over every reachable adjacency. `E-INCOMPARABLE` and `E-AMBIGUOUS` are expected to be absent; any occurrence needs a `resolve` record before the gate passes.
6. **Dead policy absent or explained** — deferred-partner records are reported as a list; each record dead within the alphabet must carry a written explanation.
7. **Outcome-partition invariant and GSUB offset headroom** — the crate’s fold asserts the outcome partition on every configuration’s table; read-back fails the build when the GSUB subtable-offset headroom falls below `readback.SUBTABLE_OFFSET_HEADROOM_FLOOR`, recorded under `gsub_budget` in `readback_summary.json`.
8. **Oracle conformance** (§6 above) — no subset baseline row matches more than one ledger entry (`multi_matched == 0`), across every configuration in `conform.ACCEPTANCE_CONFIGS`; the coverage of ss06, ss07, and ss06+ss07 rests on the identity checked in step 1 of the procedure above. Unmatched rows are informational and wait on verdicts on the review surface.
9. **Font conformance** — the per-edit belt: an exhaustive HarfBuzz sweep to the conform horizon (default 4) against settlement, exact (no ledger), with split-buffer equivalence and gap-0 pen positions. Rule coverage belongs to read-back, the crate’s fold-time first-match check, and the build’s witness stage over the crate’s rule certificates. Read-back also checks that the boundary glyphs are inert (no substituted position admits `uni200C` or `space`, zero advance, no outline). `make conform-deep` runs the same sweep at horizon 5+ on demand. The CoreText smoke passes over the extended sequence set.
10. **Ductus parity and draft flags** — every stance names a motion; every drafted motion carries `# DRAFT — pending author sign-off`; the sign-off worklist is those markers in the rune files, found by grep.
11. **Formatting** — `make prettier`; `markdownlint-cli2` clean over new `.md` files.
12. **Repo hygiene** — changes to the old pipeline keep its output byte-identical (gate 1); new code stays under `rebuild/`, runes under `glyph_data/runes/`, scratch under `tmp/`.

## 8. Ductus drafting protocol

This follows the ductus conventions in `AGENTS.md`. The canonical ductus is at the family level, which in a rune file is the top-level `ductus:` map. A stance gets its own ductus only for a different pen motion. Several valid drawing orders are `-` bullets within one motion. Join constraints are never ductus; they are `pairings` and `unlocks` data. Drafting sources, in order: the old YAML’s ductus entries, the bitmaps, the Manual’s general Writing section (it has no per-letter stroke prose; Recon A §4), and `doc/core-idea.md`. The flag rule: **any motion whose prose is not copied byte for byte from the old YAML carries a trailing `# DRAFT — pending author sign-off` comment on its key line.** The rune files are the sign-off list; grep for `# DRAFT`. Typo fixes (“recieve”, a mid-sentence capital “Then”) count as edits and so as drafts.

How the first batch was drafted:

- **qsPea** — motions `full` and `half` only. The old font’s third and fourth “dipping” motions are §4 bindings (the same pen motion with join-conditioned attachment ink), so their prose folds into the two motions’ descriptions. They are not motions, or ductus parity would require stances for them.
- **qsTea** — the old YAML had no ductus for it. Its motions are `full` (the Tall bar, written top to bottom or bottom to top: two bullets of one motion, as for ·It) and `half` (the stroke stopped at the x-height).
- **qsIt** — one motion (`hapax`). Bullet 1 of the old prose (“Either written from top to bottom or bottom to top.”) is copied verbatim; bullets 2–4 are join constraints and became `pairings: only:` and the ss04 unlock. With the prose byte-identical, the motion carries no DRAFT flag; the structural move still needs sign-off.
- **qsMay** — motions `loop` (the old prose with the mid-sentence “Then” fixed) and `grounded-loop` (the reachable `exits_at_baseline` drawing).
- **qsOy** — the Manual’s clean-pen note about small loops is context, not stroke prose.
- **Ligatures** — a ligature rune carries its own ductus (§5.7); `qsTea_qsOy`’s is the ·Tea bar flowing into the ·Oy loop.

## 9. The milestone’s record

There is no closing report. The milestone’s record is the commit history plus the runes’ `why:` fields. The durable design facts (the semantics-coverage section, the ductus-drafting protocol, the `rebuild/script.yaml` location deviation, and the design facts below) are in this plan, and the dead-policy and deferred-partner lists are in `rebuild/out/m1/pipeline_summary.json`.

## Design facts and exceptions, as built

### The spec front end

- **Schema validation does not need `jsonschema`.** `jsonschema` is not a project dependency, so `spec_load` has a small built-in evaluator driven by the JSON Schema files. It covers the keyword subset those files use, and an unrecognized keyword is a hard error. `test_jsonschema_agrees_with_builtin_checker` compares the two whenever `jsonschema` is importable (under `uv run --with jsonschema`). The schema files are the only source of the rules.
- **`SpecError` carries a list.** The contract names `SpecError(file, path, message)`. The implemented signature is `SpecError(file, path, message, line=None, issues=None)` with an `issues: tuple[SpecIssue, ...]` attribute (`SpecIssue(file, path, message, line)`), because collecting all errors before raising needs a place to keep them. Single-issue construction matches the contract shape.
- **Unlock tagging is a separate function.** `enumerate_cells` returns plain `CellId`s per the contract. `surface.enumerate_cells_with_unlocks` (pairs of cell and unlock records) and `surface.unlocks_for_cell` meet the “returned tagged with their unlock record” requirement. `CellPlan.unlock` (a single record in `model.py`) holds the first granting unlock when several grant one cell; `unlocks_for_cell` returns them all.
- **E-ANCHOR lives only in `defects.run_gates`.** `_check_anchors` checks every realized `GlyphRecord`, the resolved per-cell bitmap after stubs and adjustments, so the dip anchors (qsPea) are checked against the drawing §3.2 requires. `_check_parity_anchors` checks `selectable: false` rows, which realize into no cell, against their stance’s base drawing. A row’s `x_off_convention` exempts only its own side. `withdrawal: safe` verification belongs to Group 3, through `CellPlan.safety_checks`.
- **Two `model.py` narrowings are kept as they are.** (a) `Policy.groups` resolves rune-local groups to family-grain `frozenset[str]`, so a trait- or stance-qualified group atom (`{family: qsDay, trait: half}`) is widened to the bare family with a `SpecWarning`. Group 2’s matching sees families only, which is conservative for the ss04 veto carve-out. The `SpecWarning` flags the first rune whose qualified atom needs to discriminate. (b) `Condition` has no trait axis, so a trait qualifier in a condition is a load error (“not representable”) instead of a silent widening.
- **`resolve:` records load in the against-a-named-record form only** (`{against: {rune, id}, when, pick, why}`, §5.8). The floor form (`at:`) and case-group promotion are rejected at load with an explicit error, and a resolve’s `when:` may not carry `self:`.
- **Unlock-added rows get their anchor by convention** (entry x = min ink, exit x = max ink + 1, from the stance’s base bitmap), because the documented unlock shape has no `x:` (the authoring caveat on qsTea full’s ss03 x-height entry). A base bitmap with no ink at the unlocked height raises a `SpecError`.
- **Vacuous pairings are warnings, not errors** (a `never:` row naming a side the stance never declares), per the authoring caveat that `spec_load` should tolerate or drop the row. Pairings filter two-sided cells only, per §3.2’s “one-sided and isolated cells always exist”; qsIt’s `only:` rows naming `none` sides are redundant but harmless.

### Semantics

- **`LeftContext` and `RightToken` live in `rebuild/pipeline/settle.py`**, not in `model.py`, whose frozen block has no window frames. The plan’s `transition` is the crate’s subcommand. A caller poses one window through `kernel_exec.settle_cases` (or `settle_windows` / `settle_sequences` for a batch) and reads back the fuller trace: candidate table, eliminations, deciding stage, joint flag, and prospect. `kernel_exec.trace_of` decodes it into `settle.TransitionTrace`, which `table` and `explain` read.
- **The withdrawn-exit cell state is encoded in the adjustments grammar.** `CellId.exit` is `Height | None` with no withdrawn token, so a mid-word declined exit whose row binds a named withdrawal bitmap settles as exit `None` plus an `ex-bind-<bitmap>` adjustment. An explicit `cells:` composition for `(entry-state, height-withdrawn)` overrides the row binding. `withdrawal: safe` rows collapse to the plain exit-none cell, and at a boundary the exit was never declined, so no token is emitted. Geometry’s `bind` op applies the same substitution, and `surface.resolve_cell`’s withdrawal-binding resolution and the token must be treated as one binding, never stacked.
- **Reachability conditioned on the follower makes the fixpoint window-exact.** A settled left state is enqueued only against the `right1` that was the producing window’s `right2`. An entry refusal or unlock conditioned on the follower (qsTea’s half x-height entry refused before qsTea under ss03) makes other combinations contradictory, and a naive enumeration produces unreachable windows that raise `E-STRANDED`. Windows that formation makes impossible (an adjacent ligature pair surviving unformed) are excluded too.
- **ZWNJ-locked inputs enumerate under the chokepoint twin’s name**, `model.locked_glyph_name` (`<raw>.noentry`), locked before settlement as in the prototype. This keeps each plain input’s boundary-left outcomes in one block, and the emitter, the raw-pipeline replay, and glyph minting all use the same name. In the settled output, `locked` is a `CellId.adjustments` token, which cell labels write out (`qsTea.half.ex-y5.locked`). The boundary lookahead class is `(uni200C, space, periodcentered)`: the namer dot is in it because it has no join surface, although it does not split a run for word position.
- **`build_tables(spec, features)` runs per configuration, with labels that do not depend on the configuration.** Marker folding and the no-conflict assertion are in `emit_gsub`, per the plan’s stage list. The outcome-partition assertion is the crate’s `fold::assert_outcome_partition`: partition disjointness plus the first-match-wins replay of every reachable transition. Joint rows combine the floor-broken realization ties with the table-level comparison of the optimistic prospect against the settled follower.
- **Ranking-stage interpretation, fixed by tests:** `order:` (stage 4) applies across stances before the structural floor, so a non-joining preferred stance beats another stance’s grounded join with an equal join count (qsMay before qsTea+qsIt under default). The weak lead preference (stage 6) is unreachable because the floor is total, so it is documented and not coded. Cross-rune prefer conflicts at non-nested specificity raise `E-INCOMPARABLE`, and same-rune ones `E-AMBIGUOUS`. Non-nested extend overlaps with equal demands are tolerated, with `ok:` normalized to its `[by, by]` default before demands are compared.
- **`fixtures.py`’s mini-spec must carry every record that fires inside the alphabet.** qsTea’s full-baseline-entry refusal is the example: without it ·Tea·Tea, ·Pea·Tea, and an entered ·It·Tea join, contradicting the authored YAML. A record may be left out only where it is vacuous over the alphabet.
- **Authored-data findings are asserted as authored, never quietly fixed.** `rebuild/test_settle.py` asserts them over the fixture spec on rows marked AUTHORED-DATA FINDING. One is qsMay’s declined exit mid-word, which renders with the fixture’s pulled-back withdrawal binding; `qsMay.yaml` declares no withdrawal binding. Another is qsIt’s baseline-exit refusal toward qsTea, qsRoe, and qsIt, which applies only to unentered cells, so an entered ·It joins a following ·It at the baseline where the old font breaks.

### Realization and conformance

- **`model.py` holds shared definitions beyond the plan’s frozen block, documented in its docstring:** the generated adjustments grammar (`locked`, `en/ex-ext-N`, `en/ex-con-N`, `en/ex-trim-N`, `en/ex-bind-<bitmap>`) that settlement writes and geometry reads; `relevant_marker_features`, `marker_glyph_name`, `locked_glyph_name`, and `raw_rename_map`, so the table builder, the emitter, and conform agree on marker-twin and chokepoint-twin names without a cross-group import; and `CellPlan` and `GlyphRecord`, the two cross-group artifacts the plan names.
- **The isolated cell’s display name is the bare rune name.** The raw cmap glyph must be named exactly `qsMay`, so `geometry.isolated_cell` defines the isolated cell as the default stance with no entry and with the exit whose connector ink is part of the base drawing (marked by a `withdrawal:` binding to a named drawing; `safe` means the base drawing is already withdrawn), and `geometry.display_name` names that cell bare. A cell whose exit is withdrawn relative to its stance’s base drawing gets an `ex-wd` part so names stay unique. Names are never parsed.
- **`emit_gsub` takes optional inputs beyond the plan’s two-argument form.** `glyphs` (the realized inventory) supplies the glyph names the rules are checked against and the namer-dot follower class; without it the namer-dot stage is left out. `ss10_twins` (raw cmap glyph name → anchor-free `.ss10` twin name) feeds the ss10 pre-empt; without it the pre-empt is left out and the FEA says so in a comment. `namer_dot` names the dot and lowered-dot pair. `emit_gpos` likewise takes `spec` for the locked-twin NULL/NULL parity registrations, which the glyph mapping alone cannot supply. A height with no anchors in the supplied glyph set gets no curs lookup; the migrated runes declare rows at all four heights, so the M1 build emits all four.
- **`defects.run_gates` duck-types Group 2’s tables.** `tables_by_config` values may be `(DecisionTable, TreatyTable)` pairs or single objects. Treaty endpoints may be `CellId`s or string labels, resolved through an index over both the generated display names and `table.cell_label` shapes, and the join attribute may be `join` or `junction`, with `"break"` meaning no join. The extension-band check is coarse at M1 (exact per-record static sanity; per seam, against the union of the candidate bands on the pair’s runes), and dead-policy use is judged by provenance citations in table rules and treaty rows. The module docstring records both limits.
- **Rule coverage is not the sweep’s job (the §10 tier-3 obligation).** Read-back checks per build that the compiled font holds every planned rule. The build’s witness stage (`run_m1.run_rule_witnesses`) settles the certificate the crate builds from the rows’ own producer chains for every rule (`rebuild/kernel-rs/src/certificate.rs`), which shows every settlement rule is reachable. The static half, that no rule sits behind another and never wins a window, is checked in the crate’s fold, which fails the build on such a table. So `ConformReport` has no coverage accounting, and the sweep is the per-edit belt at horizon 4: it samples HarfBuzz application semantics and the sufficiency of the window abstraction. `make conform-deep` runs the same sweep at horizon 5+ on demand. It is armed by `emit_gsub.behavior_classes` (which fails on any rule shape it does not recognize), the font-compilation code, and the uharfbuzz version. Where the deep slots enumerate at class grain, a witnessed row stands for a fiber of label windows the build has checked. Emission checks coverage of the emitted list (`emit_gsub._assert_fold_sources`: every table rule of every configuration folds into exactly one emitted row), so a certified table rule is a certified shipped row. The witness stage runs on the tables the build just folded, so it never sees a stale enumeration.
- **`compare_against_baseline` compares ligation, seams, cell identity, and positions (§6 step 2(d)).** The oracle shapes every seam- and ligation-identical row with the new font and diffs drawn positions against the baseline: per-slot glyph origins plus the run’s total advance, because the two fonts can split a seam differently between the left glyph’s advance and the right glyph’s x_offset. Sidecar kerns are normalized out through `oracle_positions.KernEvaluator`. `uni200C` is default-ignorable, so the old font kerns across it, and the normalization’s kern partner skips ZWNJ slots accordingly. Rows whose matched cell-grain ledger class legitimately redraws ink are excluded and counted; `ink_identical: true` in the ledger marks the classes whose claim the position channel checks. Ledger counts go to `BaselineReport` and `divergence-audit.tsv`; the run never rewrites the ledger YAML.
- **`compile_font` uses the prototype’s metadata dict under the family name `AbbotsMortonSpaceportM1`** (metric parity with `glyph_data/metadata.yaml` is left to integration) and adds `space` and `uni200C` records when they are absent. `pack_gsub` repacks the settlement lookup’s format-3 subtables into shared-ClassDef format-2 groups before the first save, and the settlement lookup uses GSUB type 7 Extension offsets (`useExtension`). Read-back holds the subtable-offset headroom to `readback.SUBTABLE_OFFSET_HEADROOM_FLOOR`.
- **The read-back stage uses two recorded signature extensions.** `GsubPlan` carries structured per-stage expectations filled in at emission: the ss10 pre-empt map, the guarded and plain formation rows, the per-feature marker substitutions, the folded and ordered settlement rules, the namer-dot stage, and the calt stage list. `readback.verify_font` therefore compares the re-parsed font with the same in-memory data that produced the FEA text, not with a re-derivation that could disagree. `emit_gpos.cursive_registrations` exposes the per-height anchor registry the curs lookups render, in font units, as the GPOS expectation. `run_m1` verifies the font immediately after `build_mini_font`, writes `readback_summary.json` beside the other per-gate summaries, and raises `ReadbackError` on divergence. The scope is the back half: FEA text → feaLib → packing → serialization → re-parsed bytes. The fold → FEA front half keeps its emission-time assertions and is covered by conform.
- **`coretext_smoke` has its own copy of the Swift harness in `rebuild/pipeline/`**, extends the sequence set (`smoke_sequences_m1.txt`: qsPea rows, migrated-family seams, qsTea_qsOy windows, namer-dot rows), and runs each sequence under every configuration in `coretext_smoke.FEATURE_CONFIGURATIONS`, which lists the same configurations as `conform.ACCEPTANCE_CONFIGS`.

### Integration

- **The configuration fold renames raw labels per configuration and orders the merged rules.** `model.raw_rename_map` maps each rune whose own unlocks the configuration’s sets touch to its marker twin (and `<rune>.noentry` to `<marker>.noentry`). `emit_gsub._fold_rules` renames each configuration’s rules through it before the exact-duplicate union, which is what makes the fold conflict-free (a conflict raises `EmitError`). `emit_gsub._ordered_settle_rules` then orders each input’s merged rules: backtracked rules first, and within each block, rules whose lookahead names a marker twin before bare-label rules. This is sound because marker substitution is unconditional, so a marker label and the bare label it replaces never appear in the same stream. `conform.absorb_replay_memo` reads the crate’s window labels through the same map.
- **Boundary withdrawal semantics are the same across surface, settlement, and geometry.** `surface.resolve_cell` does not apply `withdrawal:` side bindings to the token-less exit-none cell: that cell is the boundary rendering (the base drawing, dangling ink and all; the prototype’s anchor_kept_at_boundary). The mid-word declined exit arrives as settlement’s `ex-bind-<bitmap>` adjustment and resolves to the bound drawing. A live exit at a different height still takes the withdrawal binding implicitly, which keeps the side-binding-disagreement build error in force.
- **The ss10 overlay is modeled, not settled.** The emitter’s pre-empt substitutes every letter’s cmap glyph with its anchor-free `.ss10` twin before formation, so under ss10 nothing forms, settles, or attaches, and the configuration has no settlement table (`conform.OVERLAY_CONFIGS`). The belt sweeps it to `conform.OVERLAY_HORIZON` behind read-back’s isolation check, and the oracle compares its rows with the bare stream (every letter its default-stance cell, every seam a break). The old font’s ss10 isolates every letter the same way, through its own anchor-free `.ss10` twins, so the classifier gives no class to an ss10 row off a boundary, and any such divergence waits for review. The namer dot lowers with ZWNJ transparent to the match, as in the old font (baseline row 00B7:200C:E670), so the split-buffer check treats the two dot forms as one slot signature and the oracle’s name comparison folds `periodcentered.lowered` into `periodcentered`.
- **The old font’s behavior outranks a literal reading of its YAML when the two disagree.** ·May·Tea breaks while ·May·May joins, and the off-anchor contact gate rejects that join on its own, so qsMay’s grounded-exit refusals name ·Tea. Findings the gates do not contradict stay as authored and go in the ledger.
- **Scope a `from:` or `toward:` list from the baseline TSV, never from reading FEA.** The old font joins a bare pair by GPOS cursive attachment alone whenever both bare glyphs carry anchors at the same height. No calt rule fires, so an FEA grep reports the pair as having no interaction, and a scope written from that reading drops real joins in both directions. The length-2 rows of `rebuild/out/m1/baseline-<config>.subset.tsv.gz` are the definitive pair-level join map.
- **Accepted off-anchor contacts are listed in `rebuild/m1-contact-allow.yaml`** (checked in and human-reviewed, like the ledger): one signature per contact the old font already draws on a join the baseline shows.
- **The divergence ledger matches through one classifier.** `oracle.classify_divergence` assigns each divergent row a single class from its phenomenon set (per-position alias-vs-settled cell differences plus seam gains and losses), and most ledger predicates are `classify(row) == id`, so those classes partition the rows by construction. Two predicates (`kern_channel_out_of_scope`, `may_ligature_seam_loosened`) test rows directly, for rows the classifier leaves unassigned. Per-entry counts are written to `divergence-audit.tsv` and `oracle_summary.json`.
- **`rebuild/pipeline/run_m1.py` is the integration driver** (`uv run python -m rebuild.pipeline.run_m1`): tables and TSVs, glyph minting (settled cells named by `settle.cell_label`, so rules and glyphs agree by construction; raw cmap glyphs carry no curs anchors), defect gates (`defects.run_gates` with the reviewed allow-list), emission, the mini-font build, the post-compile read-back, then the oracle gate. Font-side conformance runs through `run_m1 --conform-only` (per-configuration sharding with `--jobs`, horizon from `--conform-horizon`, default 4). The artifact cycle runs it as `gate:conform`, and `make conform-deep` runs the same sweep at horizon 5+ under its own green record keyed on the behavior classes. The CoreText smoke runs through `python -m rebuild.pipeline.coretext_smoke`.
