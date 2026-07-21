# M1 plan: first runes (qsIt, qsTea, qsPea, qsMay) and the §14 module skeleton

Decisions for milestone M1, made on the evidence in `rebuild/recon/m1-families.md` (Recon A), `rebuild/recon/m1-integration.md` (Recon B), `prototype/PLAN.md` + `prototype/REPORT.md`, and `rebuild/BASELINE-REPORT.md` — all since deleted per the note-taking rules, so those citations (and the Recon A / Recon B / REPORT shorthand throughout this plan) resolve only in git history. The design doc (`doc/rebuild-design.md`) is binding throughout; section references below are to it unless marked otherwise. Two prototype follow-ups are binding constraints on this plan: the outcome-partition property is a hard build invariant (REPORT follow-up 1), and Extension promotion is a watched yellow flag in the budget gate (follow-up 2).

Hard rules restated: all new code under `rebuild/`; the old pipeline stays byte-identical (Senior Sans OTF SHA-256 `3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35`); Python via `uv run` only; broad test runs always `-n auto --dist worksteal`; nothing committed or staged; American English; “isolation”/“isolated” never abbreviated; no hard-wrapped comments or docstrings; YAML formatting per the repo CLAUDE.md.

## 1. Locations

**Rune files: `glyph_data/runes/` — the final §2 address, used now.** Recon B proved the old pipeline’s only YAML discovery is the non-recursive `path.glob("*.yaml")` in `tools/build_font.py:91`, so files in `glyph_data/runes/` are invisible to `make all`, every test, and every tool. No later move, no deviation to record. M1 creates: `qsPea.yaml`, `qsTea.yaml`, `qsTea_qsOy.yaml`, `qsMay.yaml`, `qsIt.yaml`, `qsOy.yaml` (code-point order: E650, E652, E652+E679 ligature after its lead, E665, E670, E679).

**Registry: `rebuild/script.yaml` — a recorded deviation from §2, until cutover.** `glyph_data/script.yaml` is proven UNSAFE today: `load_glyph_data` classifies any document without a `_STRUCTURAL_KEYS` key as a bare Senior kerning rule and `generate_kern_fea` KeyErrors on it, killing the old build. The registry parks under `rebuild/` and moves to `glyph_data/script.yaml` at cutover; `spec_load` takes both paths explicitly (no globbing for the registry), so the move is a one-line call-site change. Deviation recorded here.

**Schemas: `rebuild/schema/rune.schema.json` and `rebuild/schema/script.schema.json`** (JSON Schema 2020-12), validated at load time via `uv run --with jsonschema` (the existing `--with black` zero-footprint pattern; no `pyproject.toml`/`uv.lock` edit during M1). A `.vscode/settings.json` `yaml.schemas` mapping is deferred to cutover — M1 touches nothing outside `rebuild/` and `glyph_data/runes/`.

**Modules: `rebuild/pipeline/` as a package**, mirroring §14 names:

```text
rebuild/pipeline/
  __init__.py
  model.py            shared frozen dataclasses — the contract all three implementation groups code against
  spec_load.py        YAML → ResolvedSpec; schema validation; lints (naming, ductus parity, right.then, dead-reference)
  surface.py          cell enumeration; binding resolution; pairings/unlocks/scopes → CellPlan
  settle.py           the §6.1 settlement function (promoted from prototype/settle.py)
  table.py            decision + treaty tables; outcome partition; E-STRANDED; joint flags (promoted from prototype/table.py)
  geometry.py         per-cell bitmap/anchor realization; stubs; bindings; extensions; gap arithmetic
  defects.py          E-DANGLE, E-UNREALIZED, E-ANCHOR, off-anchor contact, dead policy
  emit_gsub.py        four-stage GSUB + namer-dot stage (promoted from prototype/emit.py)
  emit_gpos.py        per-height curs lookups
  compile_font.py     mini-font build via build_font(senior_fea=...) + budget gate (promoted from prototype/build.py)
  conform.py          HarfBuzz per-transition gate, exhaustive sweep, baseline-oracle comparison, ledger matching
  explain.py          the §6.3a CLI (python -m rebuild.pipeline.explain)
  baseline_subset.py  one-time streaming filter of rebuild/out/baseline-*.tsv.gz to the M1 sub-alphabet
  coretext_smoke.py   CoreText-vs-HarfBuzz driver (prototype recipe, extended sequence set)
```

Tests live at `rebuild/test_<module>.py` (matching the existing `rebuild/test_extractor.py` convention; pytest’s `testpaths` excludes `rebuild/`, so they run via explicit `uv run pytest rebuild/ -n auto --dist worksteal`). Build artifacts go to `rebuild/out/m1/` (covered by the existing `rebuild/out/` gitignore): `M1.otf`, `M1.fea`, `settlement-<config>.tsv`, `treaties-<config>.tsv`, `surfaces/`, `budget.json`, `conform_summary.json`, `divergence-audit.tsv`, filtered baseline sub-tables. The divergence ledger `rebuild/m1-divergences.yaml` and the alias map `rebuild/m1-aliases.yaml` are committed-shape source files (human-reviewed; committed only with user approval, like everything else). `make prettier` does not cover `rebuild/`, so after any Python change M1 runs both `make prettier` and `uv run --with black black -q rebuild/` (line length 110). The §14 `review` module (treaty-diff verdict app) is out of M1 scope; the divergence ledger is its precursor artifact.

**Immediately after creating `glyph_data/runes/`**, re-verify the old build once: `make all`, then `shasum -a 256 site/AbbotsMortonSpaceportSansSenior-Regular.otf` against `3211a7a7…25cf35`.

## 2. M1 scope

**Families: qsPea, qsTea, qsMay, qsIt, plus qsOy as a fully modeled fifth rune, plus the qsTea_qsOy ligature. qsOut and qsOut_qsTea are excluded.**

The decision the task requires, made: Recon A proved the strict four-family alphabet is formation-closed with zero ligatures, but the entryless-ligature predecessor-withdrawal seam (qsTea_qsOy, §5.7) is the single most architecture-proving behavior available and the prototype already de-risked it — so qsTea_qsOy is in. Its trailing component qsOy is outside the four, which forces the choice the task names, and we take **option 1: pull qsOy’s rune file into scope as a full fifth rune** rather than shrinking the conformance alphabet. Justification: qsOy is tiny (bare form plus one x-height-entry stance plus its locked twin — its records restricted to the subset are a page); admitting it keeps the conformance gate **total** over the alphabet with no carve-out machinery (option 2’s “qsOy only immediately after qsTea” restriction is itself new code plus a documented hole); and it buys real baseline windows that must conform anyway once qsOy is typeable (·May·Oy joins at the x-height, ·Pea·Oy breaks, ·Tea·Oy forms — all baseline-verified). The prototype modeled qsOy inert (its deviation 4); M1 models it fully, so those prototype deviations do not carry forward — qsOy’s real joins are conformance obligations. qsOut_qsTea stays out: its interesting edge (the after-·See `bind:` shape) needs qsSee, and with qsOut outside the alphabet it can never form in M1 windows.

**Conformance alphabet (8 symbols):** `0x0020` space, `0x00B7` namer dot, `0x200C` ZWNJ, `0xE650` qsPea, `0xE652` qsTea, `0xE665` qsMay, `0xE670` qsIt, `0xE679` qsOy. Formation-closed: the only ligature sequence with both components in the alphabet is (qsTea, qsOy). 4,680 baseline rows per configuration at depth ≤ 4.

**Configurations.** All 11 baseline tables get filtered (cheap, one streaming pass), but the acceptance gate runs on the 8 that can affect the subset: `default`, `ss02` (·Tea x-height entry after ·I — the trigger is out of alphabet, so this should match default over the subset; asserted, not assumed), `ss03` (half-Tea entry widening; qsMay’s gated exit extension toward qsTea; qsTea_qsOy formation staging), `ss04` (qsIt’s four baseline-pairing unlocks), `ss05` (·Tea both-baseline after ·Et — trigger out of alphabet; asserted like ss02), `ss02+ss03`, `ss02+ss03+ss05` (multi-set union semantics and composite markers on qsTea — a genuine step past the prototype), and `ss10` (the global isolated overlay, in scope because it is auto-generated cell → isolated cell and keeping it makes the gate honest across the one taste set that touches every rune). `ss06`, `ss07`, and `ss06+ss07` live entirely on other families: their filtered sub-tables are asserted row-identical to `default`’s, which makes the default run cover them; any difference is a triage finding, not a silent skip.

**Kerning is out of M1 settlement scope** (the 24 sidecar records touching the four migrate via §12 later). The mini-font emits no kerning; the baseline `positions` comparison therefore normalizes out sidecar kerns by evaluating `glyph_data/senior_quikscript_kerning.yaml` read-only over subset pairs and adding the expected kern back before diffing. Any residual position divergence is real. `edge-vs-zwnj` kern triage rows touching the subset are explicit ledger entries, never auto-accepted (§12 keeps ZWNJ kerns as a proven pattern).

**The namer dot is in scope as a token**: M1’s emitter supplies its own dot-lowering mini-calt stage (Recon B proved `_namer_dot_calt_fea` is a no-op on the `senior_fea` path), and the glyph set includes `periodcentered` and `periodcentered.lowered`. No subset record conditions on `is: namer-dot`, so the condition axis is registered but unexercised (recorded here).

## 3. The rune-file template

A composite skeleton showing every §3 construct the five runes need. No single rune uses all of it; per-construct annotations name the rune that does. Formatting rules, inlined as comments where they bind: double-quoted bitmap rows; bare trailing `#` markers on the rows whose glyph-space y is 5 and 0 (which rows depends on `y_offset`); short lists inline; family lists in code-point order (qsPea, qsBay, …, qsOoze); `when:` always spelled out; no hard-wrapped prose.

```yaml
rune: qsMay
codepoint: 0xE665                  # ligature files declare `sequence: [qsTea, qsOy]` instead, no codepoint
ductus:                            # closed enumeration of motions; every stance names one; see §8 of this plan
  loop: |                          # DRAFT — pending author sign-off
    Written clockwise, starting from the leftmost pixel at the baseline. It continues right, loops around underneath the baseline, and then exits at the x-height on the right.
  grounded-loop: |                 # DRAFT — pending author sign-off
    As the loop, but the final stroke stays down and rests on the baseline at the right.
  counterclockwise:
    unrealized: true               # enumerated but undrawn (core-idea.md line 141); the honest closed-set ledger
notes: |
  Optional prose; never load-bearing. Join constraints live in pairings, not sentences.
mono: {bitmap: ["..."], y_offset: -3}    # mono-only drawing, carried for the mono font; no anchors
stances:
  loop:
    motion: loop                   # build error if missing or dangling
    traits: []                     # [half] / [alt] survive for data-expect compatibility (qsPea.half, qsTea.half)
    bitmap:                        # the isolated drawing; deep letter: 9 rows, y_offset -3
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
    bitmaps:                       # named hand-drawn siblings, referenced only by bindings below
      pulled-back: {bitmap: ["..."], y_offset: -3}
      pulled-back-stubless: {bitmap: ["..."], y_offset: -3}
    surface:
      entries:
        baseline: {x: 0, stroke: horizontal}
        x-height: {x: 3, stroke: horizontal, joined: pulled-back, from: [{family: qsUtter, joined_at: x-height}]}
          # `from:` = allowlist polarity (§13.3): this entry row joins only against the named scope.
          # `joined:` = side binding used when this side is live. qsPea's dips are the stub flavor instead:
          #   x-height: {x: 0, stub: {cols: [0], when: withdrawn}}  — same-row ink present only when joined.
      exits:
        x-height: {x: 5, stroke: horizontal, withdrawal: pulled-back}
          # `withdrawal:` = the form used when this side is declined mid-word; `safe` only when the compiler
          # verifies no reaching ink (qsIt's bar). qsPea.half's exit row carries the flagged oddities instead:
          #   x-height: {x: 4, ink_y: 6}            — today's exit_ink_y
          # and qsTea.half's top entry carries:  top: {x: 0, selectable: false}   — today's entry_curs_only.
      pairings:
        never: [{entry: baseline, exit: baseline}, {entry: x-height, exit: x-height}]
          # qsIt uses the only: form — the legal set is smaller than its complement:
          # only: [{entry: x-height, exit: baseline}, {entry: x-height, exit: none}, {entry: baseline, exit: x-height}, {entry: baseline, exit: none}, {entry: none, exit: x-height}, {entry: none, exit: baseline}, {entry: none, exit: none}]
      cells:
        - {entry: x-height, exit: x-height-withdrawn, bitmap: pulled-back}
          # explicit composition when two side bindings touch one cell; qsPea's both-dipped half is the
          # §3.2 case: {entry: x-height, exit: x-height, bitmap: half-dips-both-sides}
      unlocks: []
          # qsTea.full carries: {pairing: {entry: baseline, exit: baseline}, feature: ss05, when: {left: {family: qsEt}}}
          # qsTea.half carries: {entry: x-height, feature: ss03, when: {left: {class: ...widened ss03 scope...}}}
          # qsIt carries four ss04 rows: {pairing: {entry: baseline, exit: baseline}, feature: ss04, when: {left: {family: qsDay}}} etc.
      require: []                  # none of the five runes needs join-born stances (·Fee's case)
  grounded-loop:
    motion: grounded-loop
    bitmap: ["..."]
    y_offset: -3
    bitmaps:
      pulled-back-grounded: {bitmap: ["..."], y_offset: -3}
    surface:
      entries:
        x-height: {x: 3, joined: pulled-back-grounded, joined_x: 2}    # per-cell anchor override: the anchor moves with the bound form
      exits:
        baseline: {x: 4, toward: [{family: qsDay}, {family: qsSee}]}   # `toward:` = exit-side allowlist (code-point order)
policy:
  order: [loop, grounded-loop]     # stance preference; default = declaration order
  refuse:
    - {exit: baseline, when: {right: {family: [qsDay, qsThaw, qsZoo, qsYe, qsHe, qsNo, qsRoe, qsIt, qsEat, qsUtter, qsOoze]}}, why: These never receive ·May's grounded exit.}
    - {exit: baseline, when: {left: {family: qsRoe}, right: {family: qsEt}}}    # two-sided grain when needed
      # refuse/require may NOT use right.then — window-decidability, enforced by schema and lint
  prefer: []                       # the M1 subset has zero prefer records (see §5: semantics coverage)
  extend:
    - {stance: loop, exit: x-height, by: 1, ok: [1, 1], when: {right: {family: [qsDay, qsFee, qsJai, qsJay, qsRoe, qsIt]}}}
    - {stance: loop, exit: x-height, by: 1, when: {right: {family: qsTea}, feature: ss03}}    # the ss03-gated reach
    - {stance: loop, entry: baseline, by: 1, when: {left: {family: [qsPea, qsTea, qsYe, qsHe, qsIt], joined_at: baseline}}}
      # qsIt's flagship self: condition (replaces extend_exit_when_entered):
      #   {stance: hapax, exit: baseline, by: 1, when: {self: {entry: live}}}
      # target (stance, side, height) is mandatory; same-side records never sum; most specific wins (§6.2)
  contract:
    - {stance: loop, entry: x-height, bind: pulled-back-stubless, when: {left: {family: qsFee, joined_at: x-height}}, why: ·Fee's long reach-over absorbs the baseline stub; the redraw spans rows, so it is a bound shape, not arithmetic.}
      # `bind:` = hand-drawn sibling in place of same-row arithmetic; `trim: N` is the receiver-side blanking option
  resolve: []                      # expected empty in M1; any E-INCOMPARABLE/E-AMBIGUOUS lands here with migrated: provenance
  groups: {}                       # rune-local sets: {union: [...], minus: [...]} over family literals, traits, classes
```

### `rebuild/script.yaml` contents

Registries only, never policy (§2):

```yaml
heights: {baseline: 0, x-height: 5, y6: 6, top: 8}
  # closed; qsPea's ·Pea·Pea chain makes y6 live in M1, so all four curs lookups are emitted (unlike the prototype's three)
boundary_tokens:
  space: {codepoint: 0x0020, splits_runs: true}
  zwnj: {codepoint: 0x200C, splits_runs: true}
  namer-dot: {codepoint: 0x00B7, splits_runs: false}    # addressable as `is: namer-dot`, never splits (§3.4)
features:
  ss02: {kind: capability, description: "·Tea x-height entry after ·I"}
  ss03: {kind: capability, description: "x-height exiters reach half-·Tea"}
  ss04: {kind: capability, description: "·It same-height baseline pass-through"}
  ss05: {kind: capability, description: "·Tea both-baseline after ·Et"}
  ss10: {kind: taste, description: "isolated forms overlay", overlay: isolated}
  interactions: [[ss02, ss03], [ss02, ss03, ss05]]      # the declared, conformance-verified combinations
predicate_classes:                 # derived-only: computable expressions, never hand-membered (§2)
  halves-that-exit-at-x-height: {all: [{trait: half}, {can_exit_at: x-height}]}
  can-enter-at-baseline: {can_enter_at: baseline}
  can-enter-at-x-height: {can_enter_at: x-height}
  can-exit-at-baseline: {can_exit_at: baseline}
  can-exit-at-x-height: {can_exit_at: x-height}
  talls: {height_class: tall}
  shorts: {height_class: short}
  deeps: {height_class: deep}
  # the `no_pea` variant of the halves set is written at the use site as class + except, not a second class
families:                          # the full name registry, so conditions naming unmigrated families validate
  qsPea: {codepoint: 0xE650}
  qsBay: {codepoint: 0xE651}
  # ... all 44 runes + the 13 ligature names (ligatures: {sequence: [...]}) in code-point order ...
```

The `families` registry is what lets the dead-policy gate distinguish a **deferred-partner record** (condition references only families with no rune file yet — e.g. qsPea’s baseline entry `from: [qsEt, qsAwe]`) from genuine dead policy. Deferred-partner records are listed by the gate, not failed.

## 4. The JSON schema

Files: `rebuild/schema/rune.schema.json`, `rebuild/schema/script.schema.json` (draft 2020-12). Validation runs in `spec_load` via `uv run --with jsonschema`. What the schema enforces mechanically:

- **The closed `when:` vocabulary** — `$defs.when` with exactly `left`, `right`, `self`, `word`, `feature`, `additionalProperties: false` at every level. `left` admits `family`, `class`, `stance`, `joined_at`, `stroke`, `is`, `except`; `right` admits `family`, `class`, `stroke`, `is`, `except`, `then` — where `then` is the static side-condition shape **without** `then` (one hop, no recursion). `self` admits `{entry: live|none, exit: live|none}`. `word` is the enum `initial|medial|final|isolated`. An eighth axis is structurally unwritable.
- **The refuse/require `right.then` prohibition** — `refuse` and `require` records reference `$defs/when_window_decidable`, whose `right` definition has no `then` property. `spec_load` re-checks it in Python so the error message can cite the design rule (window decidability one position left, §3.3).
- **The stance-ID lint** — stance keys (and `motion` names) use `propertyNames: {not: {pattern: "(before|after|noentry|noexit|nonjoining|ss[0-9])"}}`, duplicated as a Python lint in `spec_load` with a readable message. Generated display names are exempt by construction: authored data has no field that holds one.
- **Structural shapes** — `pairings` is `{never: [...]}` and/or `{only: [...]}` of `{entry, exit}` pairs over registered heights ∪ `none`; `cells:` rows require `bitmap:` (the explicit binding is the point) with optional `entry_x`/`exit_x`; `unlocks` rows require `feature:` and exactly one of `entry:`/`exit:`/`pairing:`, with an optional narrowing `when:`; `extend`/`contract` require side + height (`stance:` required by Python lint whenever more than one stance offers that side and height — refuse-to-guess); `why:` is schema-required on every `resolve` and on every `prefer` with `mode: absolute` (an `if/then`); `ductus` values are either a string or `{unrealized: true}`.
- What the schema cannot express stays in `spec_load` Python: ductus parity, the cells resolution order (explicit `cells:` binding > side bindings > base bitmap; two side bindings disagreeing on a reachable cell with no explicit row is a build error naming the cell), extensional specificity, dead policy, predicate-class derivability (no hand-membered cross-rune lists), and the duplicate-rune-local-group linter.

## 5. Module contracts

All shared types live in `rebuild/pipeline/model.py`, written and frozen **first** (before the three implementation groups start); each group imports only `model.py` from outside its own territory, and any change to `model.py` is a cross-group coordination event. Every policy record, surface row, and unlock carries `provenance: Provenance(file: str, path: str)` (the YAML file and key path) — `explain`, the TSV artifacts, and the FEA comments all consume it.

```python
# model.py — the frozen contract (all dataclasses frozen, hashable where used as keys)
Height = str                                   # "baseline" | "x-height" | "y6" | "top" (registry-validated)

@dataclass(frozen=True)
class CellId:
    rune: str                                  # family name (ligatures are ordinary runes)
    stance: str
    entry: Height | None
    exit: Height | None
    adjustments: tuple[str, ...]               # ordered, generated: ("en-ext-1",), ("locked",), () — never authored

@dataclass(frozen=True)
class Settled:
    cell: CellId
    seam: Height | None                        # the committed seam toward the next position
    extension: int                             # summed connector pixels carried on this seam by this side

@dataclass(frozen=True)
class ResolvedSpec:                            # spec_load's output; the input to everything else
    runes: Mapping[str, Rune]                  # only the modeled runes (five + the ligature)
    registry: ScriptRegistry                   # heights, boundary tokens, features + interactions, predicate classes (membership resolved), full family-name registry
    # Rune carries ductus, stances (with Surface: entry/exit rows incl. scopes/bindings/oddities, pairings,
    # cells, unlocks, require) and Policy (order, refuse/prefer/extend/contract/resolve, groups), all parsed
    # and scope-expanded but not yet geometry-resolved.
```

### Group 1 — the spec front end (`spec_load`, `surface`, the schemas)

```python
# spec_load.py
def load_spec(runes_dir: Path, registry_path: Path, schema_dir: Path) -> ResolvedSpec: ...
    # jsonschema validation per file; then Python lints: stance-ID regex, ductus parity (every stance names a
    # motion, every non-unrealized motion has a stance), refuse/require right.then rejection, family references
    # resolved against the registry, predicate-class expression evaluation, duplicate rune-local-group flag.
    # Raises SpecError(file, path, message); collects all errors before raising.

# surface.py
def enumerate_cells(spec: ResolvedSpec, rune: str, features: frozenset[str]) -> tuple[CellId, ...]: ...
    # declared rows ∪ {none} per side, filtered by pairings (never/only), require, unlocks (feature + when
    # narrowing applies at settlement, so unlock cells are returned tagged with their unlock record).
def resolve_cell(spec: ResolvedSpec, cell: CellId) -> CellPlan: ...
    # binding resolution only, no ink: which named bitmap the cell uses (explicit cells: row > side bindings
    # (stub/joined/withdrawal) > base), per-cell anchor x values (joined_x/entry_x/exit_x overrides), the
    # flagged oddities (ink_y, selectable), and the E-DANGLE obligation when a declined side has no
    # withdrawal binding and is not verified safe (verification itself is geometry's, via the callback below).
```

Group 1 also authors the two schema files and ships `spec_load`’s error-message tests. It does not read bitmap ink (that is geometry); `withdrawal: safe` verification is exposed as a `CellPlan.needs_safety_check` flag that Group 3’s defects gate discharges.

### Group 2 — semantics (`settle`, `table`, `explain`)

Promotes `prototype/settle.py` and `prototype/table.py` nearly whole (Recon B’s promotion map): keep `transition()`’s entry binding with the bilateral-commitment rule and `E-STRANDED` raise, the refusal-aware lookahead closure, window join-count with the deliberately optimistic third term, the structural floor, joint flags, extension logic including same-seam suppression, run splitting, the fixpoint enumeration, outcome-partition compression, `uni200C`-first rule ordering, and the first-match-wins replay validator. Replace the hand-encoded spec with `ResolvedSpec`, the naming hacks with `CellId`, and add §6.2.

```python
# settle.py
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

# specificity (inside settle.py or its own small module, Group 2's choice — with the dedicated test class):
def outranks(spec: ResolvedSpec, a: PolicyRecord, b: PolicyRecord) -> Ordering: ...
    # extensional: every constrained axis expands to its concrete match set over the finite registry; A
    # outranks B iff subset on every axis B constrains, one strict. Non-nested overlap with conflicting
    # demands → E-INCOMPARABLE. Stratified evaluation (capability, then policy).

# table.py
def build_tables(spec: ResolvedSpec, features: frozenset[str]) -> tuple[DecisionTable, TreatyTable]: ...
class DecisionTable:
    rules: tuple[Rule, ...]                    # ordered: boundary rows with explicit uni200C first, two-slot before one-slot, identity rows omitted, slot-dropped fallback last
    def reachable_cells(self) -> frozenset[CellId]: ...
    def assert_outcome_partition(self) -> None # HARD build invariant (prototype follow-up 1): slot classes are a partition; violation fails the build
    def assert_e_stranded(self) -> None        # every committed exit has a refusal-aware acceptor at the next position
    def joint_rows(self) -> frozenset[int]     # optimistic-prospect-vs-settled divergence + floor-broken realization ties
    def write_tsv(self, path: Path) -> None    # settlement-<config>.tsv with provenance pointers (§8 artifact)
class TreatyTable:
    rows: tuple[TreatyRow, ...]                # one per reachable adjacent cell pair: join height or break, summed extension, kern (0 in M1)
    def write_tsv(self, path: Path) -> None

# explain.py — the §6.3a CLI, ships with the first migrated rune
# uv run python -m rebuild.pipeline.explain E665:E670:E665 --features ss03
def explain(spec: ResolvedSpec, codepoints: Sequence[int], features: frozenset[str]) -> ExplainReport: ...
    # per position: the full candidate table; every elimination attributed to provenance (file + record);
    # the rank comparison that chose the winner (which lexicographic stage decided, and between which two
    # candidates); rendered as aligned text. Accepts qs-names or hex codepoints, colon-separated.
```

### Group 3 — realization (`geometry`, `defects`, `emit_gsub`, `emit_gpos`, `compile_font`, `conform`, `baseline_subset`, `coretext_smoke`)

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
    # extension band (ok:), dead policy. Dead policy partitions into: (a) deferred-partner records — every
    # family the condition can match lacks a rune file (reported, not failed); (b) genuinely dead within the
    # modeled alphabet — asserted empty or carried in the report with a written explanation (expected M1
    # members: qsPea's baseline-entry row, whose from-scope {qsEt, qsAwe} is wholly deferred-partner, and any
    # row scope emptied by the alphabet). Errors fail; flags report.

# emit_gsub.py / emit_gpos.py
def emit_gsub(spec, tables_by_config: Mapping[frozenset[str], DecisionTable]) -> GsubPlan: ...
    # stage order: formation → ss markers (unconditional, per set; composite markers for ss02+ss03 and
    # ss02+ss03+ss05 on qsTea) → ZWNJ chokepoint → ONE settlement lookup (per-family subtable; breaks) →
    # ss10 overlay (cell → isolated cell) → namer-dot mini-calt (supplied here; no-op on senior_fea path
    # otherwise). Invariants asserted: no locked twin or chokepoint output in any raw lookahead class; every
    # named glyph exists; per-rule provenance comments.
def emit_gpos(glyphs: Mapping[CellId, GlyphRecord]) -> str: ...
    # four per-height curs lookups (y6 is live via qsPea); NULL anchors for cross-height cells; NULL/NULL
    # parity registrations for locked twins; pixels × 50 in the drawn frame with explicit advances.

# compile_font.py
def build_mini_font(glyphs, fea: str, out_path: Path) -> Path: ...
    # the prototype's verified recipe: legacy glyphs:-only dict (qs names suffixed .prop), empty
    # glyph_families, metadata, build_font(..., variant="senior", senior_fea=fea). Then the budget gate:
    # _report_gsub_budget + direct table parse → budget.json; FAIL if fontTools falls back to per-rule
    # format-3 subtables past the 16,384 B headroom floor (outcome-partition consequence); YELLOW FLAG
    # (reported, non-fatal at M1 scale) on any GSUB type 7 Extension promotion (prototype follow-up 2).

# baseline_subset.py — one-time: stream rebuild/out/baseline-<config>.tsv.gz via
# rebuild/validation/rowmodel.iter_rows, keep rows with codepoints ⊆ alphabet, write
# rebuild/out/m1/baseline-<config>.subset.tsv.gz preserving canonical order. Same filter over
# equivalence-triage.tsv → rebuild/out/m1/triage.subset.tsv.

# conform.py
def run_conformance(font_path, spec, configs) -> ConformReport: ...
    # (a) exhaustive enumeration, length 1–5 over the 8-symbol alphabet per acceptance config, HarfBuzz vs
    #     settle() transition by transition (names via TTFont, never glyph_to_string); gap-0 pen positions;
    #     ZWNJ zero-advance/no-ink; split-buffer equivalence; RuleCoverage (every emitted rule fired).
    # (b) §10 tier 3: per decision-table transition, derive a shortest example sequence + a ZWNJ-interleaved
    #     variant; shape and diff — this is the per-transition gate proper, and it subsumes joint-row routing
    #     (joint rows additionally get the cross-product treatment over their window class).
def compare_against_baseline(spec, subset_tables_dir, alias_path, ledger_path) -> BaselineReport: ...
    # the oracle gate; procedure in §6 of this plan.

# coretext_smoke.py — prototype recipe verbatim (swiftc compile per session, hex codepoints on argv,
# resolved-name assertion, GID + position diff vs HarfBuzz); sequence set extended with qsPea rows
# (·Pea·Pea y6 chain, ·May·Pea·It both-dipped cell, ·See-less en-y6 boundary rows), the four-family seams,
# qsTea_qsOy windows, and every ss-marker configuration.
```

### Parallelization and integration

The three groups are disjoint by file and by import direction: Group 1 owns `spec_load`/`surface`/schemas, Group 2 owns `settle`/`table`/`explain` (+ the specificity test class), Group 3 owns `geometry`/`defects`/`emit_*`/`compile_font`/`conform`/`baseline_subset`/`coretext_smoke`. Everyone codes against `model.py`. Group 2 and Group 3 do not block on Group 1: their tests build `ResolvedSpec` fixtures directly from `model.py` dataclasses (a small `model.fixtures` helper holds a hand-built mini-spec, the moral successor of `prototype/spec.py`). Group 3’s conform calls `settle` only through the `model.py` signature. Phase 5 wires the real rune files through all three.

### §6.1/§6.2 semantics coverage — what the real records exercise (the durable record)

The explicit check the task asks for: **qsPea adds no prefer records** — its ten stances carry only row scopes, refusals, extends, and cascade machinery that is DEAD under settlement (`reverse_upgrade_from`). So the M1 subset, like the prototype’s, contains **zero competing prefers, zero prefers at all, zero resolves**, and no organic `E-INCOMPARABLE`/`E-AMBIGUOUS` is expected.

Exercised by real records in M1 (beyond the prototype’s list): allowlist polarity with **left resolved-state scopes** (qsPea’s x-height entry `from: [{family: qsMay, joined_at: x-height}, {family: qsUtter, …}]`); a refusal whose `when:` uses a **predicate class plus except carve-out** (qsPea’s en-y6 baseline-exit refusal toward can-enter-at-x-height except five families); **explicit `cells:` composition** (qsPea’s both-dipped half; qsMay’s pulled-back entry-live/exit-withdrawn cell); **per-cell anchor override** (qsMay `joined_x: 2`); **stub-vs-withdrawal polarity both ways** (qsPea’s dips are joined-state ink; qsMay’s connector is withdrawal-removed); the **flagged oddities** `ink_y` (qsPea.half) and `selectable: false` (qsTea en-y8); the **y6 height** (·Pea·Pea, so all four curs lookups); the **`self:` condition** (qsIt’s entered-exit extension); **ss-gated extends** (qsMay toward qsTea under ss03); **four-set unlock coverage with narrowing `when:`** (ss02/ss03/ss04/ss05) and **multi-set union composition with composite markers** (ss02+ss03, ss02+ss03+ss05 on qsTea — new past the prototype); `pairings: only:` (qsIt) and `never:` (qsTea, qsMay); predecessor withdrawal before the entryless ligature; the ss10 isolated overlay.

Unexercised by real records, recorded honestly: **prefers in both modes and both grains, `resolve` and the arbitration errors, positive `word:` records, `require`, `split:`, `trim:`**, `bind:` **at settlement level** (qsMay’s after-·Fee bound contract was retired outright at the ·Fee migration — the shipped x-height entry row already binds `pulled-back-stubless` for every enterer, so activating ·Fee took only a `from:` widening and the modeled contract could never demonstrably fire; `rebuild/pipeline/fixtures.py` keeps the record on purpose as the synthetic exemplar, so its divergence from today’s `contract: []` in `qsMay.yaml` is intended rather than a Phase 5 finding, and geometry unit-tests `bind:` from it), `is: namer-dot` conditions, `stroke:` conditions in policy, case-group promotion and the subsumption linter, late formation. **§6.2 extensional specificity is implemented in full with its dedicated regression-test class, but its two named design cases (the decline-discriminator window, the qsJay contract-vs-extend overlap) need qsThey/qsJay and run on synthetic fixture specs, not M1 rune files.** This section is the durable home of that out-of-scope list — the milestone gets no separate report.

Authoring note carried from the prototype’s probed corrections (deviation 6): where today’s YAML declares a record the baseline proves never fires on a subset window (qsIt’s entry extension after half-Tea), author the record faithfully from the YAML and let the gates decide — `E-UNREALIZED`’s gap arithmetic and the baseline comparison are the arbiters, and any divergence lands in the ledger with the probe as evidence, never as a silent spec edit.

## 6. Acceptance and the divergence ledger

### Oracle-conformance procedure

1. `baseline_subset.py` filters all 11 baseline tables (one streaming pass each; canonical row order preserved) and the triage TSV to the 8-symbol alphabet → cached sub-tables under `rebuild/out/m1/`.
2. Assert the ss06/ss07/ss06+ss07 sub-tables are row-identical to default’s (then default’s run covers them).
3. For each acceptance configuration (default, ss02, ss03, ss04, ss05, ss02+ss03, ss02+ss03+ss05, ss10): for every sub-table row, run `settle(spec, row.codepoints, features)` and compare **transition by transition**: (a) ligation via clusters; (b) every seam’s classification (join height or break) — on the new font’s side re-derived with `rebuild/validation/classify.SeamClassifier`; (c) cell identity through `rebuild/m1-aliases.yaml` (hand-written: old compiled glyph name → `CellId`, small at five families); (d) positions, kern-normalized per §2 of this plan (sidecar kerns evaluated read-only and added back).
4. Every divergent row must match **exactly one** ledger entry: zero matches fails conformance (silent divergence), two-plus matches fails the ledger (overlapping predicates). Results land in `rebuild/out/m1/divergence-audit.tsv` with per-entry counts.
5. The same comparison runs font-side: the conformance sweep (HarfBuzz vs `settle`) must be exact — the ledger applies only to the settle-vs-baseline diff, never to the font-vs-settle diff, which is a compiler defect by definition (§1).

### Ledger format — `rebuild/m1-divergences.yaml` (committed-shape, human-reviewed)

One entry per divergence **class**, with a matching predicate, the observed count, exemplar rows, and a mandatory `why:`:

```yaml
- id: zwnj-word-initial-unification
  status: intended                # intended | drift-accepted | triaged — nothing else passes review
  match: {predicate: zwnj_noentry_identity, configs: all}
    # predicate = a named matcher registered in conform.py (small, reviewed functions over the row pair);
    # structured field predicates ({window: ..., seam_change: ...}) are also legal match shapes
  count: 0                        # written by the conformance run; reviewed as a diff
  exemplars:
    - {config: default, codepoints: "200C:E650", baseline: "uni200C qsPea.noentry", new: "uni200C qsPea"}
  why: |
    Post-ZWNJ ≡ word-initial is definitional in the new model (§3.4); the .noentry shadow universe is deleted. The triage row records both sides; the new outcome must equal the row's edge-shaped side, which the matcher asserts.
```

Expected classes, drafted up front (each still needs the run’s counts and human review):

1. **zwnj-word-initial-unification** — the `.noentry` deletion; acceptance test per row: new output equals the recorded edge-shaped side of the corresponding `zwnj-vs-edge` triage row.
2. **space-vs-edge-guard-unification** — same shape for the boundary-guard asymmetry; possibly zero subset rows (the known exemplar ·Excite·Tea is out of alphabet); a zero-count entry is retired at review, not kept speculatively.
3. **stranded-exit-withdrawal** — `E-STRANDED`/lookahead-closure semantics: It·It loses today’s benign dangling ex-y5; entered ·It before an entryless follower settles exit-withdrawn (prototype divergences 1–2, generalized).
4. **same-seam-extension-non-summing** — May·It·May’s right seam matches Tea·It·May (prototype divergence 3).
5. **ss03-zwnj-leak-fixed** — `qsMay ZWNJ qsTea` + ss03 no longer joins; the one row the rebuild is required to change (prototype divergence 4; cross-shaper finding 1).
6. **marker-staging-ligature-formation** — `qsMay qsTea qsOy` + ss03 and `ZWNJ qsTea qsOy` form the ligature (markers staged after formation; prototype divergences 5 and deviation 5).
7. **ss03-chain-join-gains** — It·May·Tea / Tea·May·Tea under ss03 gain the second join under window join-count (prototype deviation 3).
8. **structural-floor-drift** — residual greedy-vs-today don’t-care drift per §15.4, the catch-all that must stay small and itemized; every member row is listed in the audit TSV and eyeballed. (Recon’s 39-row undipped `qsPea.half.ex-y5` cascade residue does not appear here — its windows contain qsFee, outside the alphabet.)
9. **kern-channel-out-of-scope** — position-only residue attributable to `edge-vs-zwnj` kern triage rows, carried as explicit per-row triage decisions (never auto-accepted), expected near-zero after kern normalization.

Anything not matching these gets triaged to ground before acceptance — a new reviewed entry with a `why:`, or a fix. Conformance passes iff every divergent row matches exactly one entry.

## 7. Gates

All must be green for M1 completion; each is a command or an assertion in `rebuild/` tests:

1. **Old-font byte identity** — `make all`; `shasum -a 256 site/AbbotsMortonSpaceportSansSenior-Regular.otf` == `3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35`. Run once right after creating `glyph_data/runes/`, and again at milestone end.
2. **`make test` green** (the untouched suite; ≈3 min under xdist — never single-threaded).
3. **`uv run pytest rebuild/ -n auto --dist worksteal` green** (existing baseline tests + all new module tests).
4. **Schema validation + lints green** — jsonschema over all six rune files + the registry; stance-ID regex; ductus parity; `right.then` prohibition; predicate-class derivability.
5. **§9 hard E-gates on the subset** — `E-STRANDED` (table invariant), `E-DANGLE`, `E-UNREALIZED` (gap 0 on every treaty join row), `E-ANCHOR`, off-anchor contact over every reachable adjacency; `E-INCOMPARABLE`/`E-AMBIGUOUS` expected zero (any occurrence needs a `resolve` with `migrated:` provenance before the gate passes).
6. **Dead policy asserted empty or explained** — deferred-partner records reported as a list; genuinely-dead-in-alphabet records must each carry a written explanation (expected: qsPea’s baseline-entry row).
7. **Outcome-partition invariant + budget gate** — `assert_outcome_partition()` on every config’s table; budget gate fails on per-rule format-3 fallback past the headroom floor and yellow-flags any Extension promotion in `budget.json`.
8. **Oracle conformance** (§6 above) — every subset baseline row matches or maps to exactly one ledger entry, across the 8 acceptance configurations, with the ss06/ss07 identity assertion.
9. **Font conformance** — exhaustive length-1–5 HarfBuzz sweep vs `settle` exact (no ledger), per-transition shortest-example gate with ZWNJ-interleaved variants, RuleCoverage total, split-buffer equivalence, gap-0 pen positions, ZWNJ structural checks; CoreText smoke green over the extended sequence set.
10. **Ductus parity + draft flags** — every stance names a motion; every drafted motion carries `# DRAFT — pending author sign-off`; the sign-off worklist is the rune files’ `# DRAFT` markers themselves, enumerated by grep.
11. **Formatting** — `make prettier` AND `uv run --with black black -q rebuild/`; `markdownlint-cli2` clean over new `.md`.
12. **Repo hygiene** — `git status` shows only additive untracked files under `rebuild/`, `glyph_data/runes/`, and `tmp/`; nothing staged; no existing pipeline file modified.

## 8. Ductus drafting protocol

Per the repo CLAUDE.md conventions: the canonical ductus lives at the family level (here: the rune file’s top-level `ductus:` map); variant-level ductus only for genuinely different pen motions; multiple valid drawing orders are `-` bullets within one motion; join constraints are never ductus (they are `pairings`/`unlocks` data). Drafting sources, in order: today’s YAML ductus entries, the bitmaps, the Manual’s general Writing section (no per-letter stroke prose exists — Recon A §4), and `doc/core-idea.md`. The flag rule: **any motion whose prose is not carried byte-for-byte from today’s YAML carries a trailing `# DRAFT — pending author sign-off` comment on its key line**, and the sign-off list is the rune files themselves — grep for `# DRAFT`; there is no separate report. Typo fixes (“recieve”, the mid-sentence capital “Then”) count as edits and therefore as drafts.

Per rune:

- **qsPea** — motions `full` and `half` only. Today’s third and fourth “dipping” motions are §4 bindings (same pen motion, join-conditioned attachment ink), so their prose folds into the two motions’ descriptions — including finally finishing the trailed-off “As in the half way, but ” sentence as part of the half motion’s joined-on-both-sides prose. They must not become motions, or ductus parity would demand stances for them. Entire entry DRAFT.
- **qsTea** — no ductus exists today (the hard migration gate). Draft motions `full` (the tall bar, written top-to-bottom or bottom-to-top — two bullets of one motion, by analogy with ·It and pending author confirmation) and `half` (the stroke stopped at the x-height). Entire entry DRAFT.
- **qsIt** — one motion; bullet 1 (“Either written from top to bottom or bottom to top.”) carries verbatim; bullets 2–4 are join constraints and move to `pairings: only:` + the ss04 unlocks. If the prose is byte-identical, no DRAFT flag on the motion itself; the structural move is still flagged for sign-off.
- **qsMay** — motions `loop` (today’s prose with the “Then” capitalization fixed → DRAFT), `grounded-loop` (new draft for the real, reachable `exits_at_baseline` drawing → DRAFT), and `counterclockwise: {unrealized: true}` (named by core-idea.md line 141; honestly enumerated, undrawn).
- **qsOy** — check `glyph_data/quikscript.yaml` for an existing entry at Phase 3; draft one if absent (the small loop letter; the Manual’s clean-pen note about small loops is context, not stroke prose). Likely entirely DRAFT.
- **qsTea_qsOy** — ligature runes carry their own ductus (§5.7); draft one (the ·Tea bar flowing into the ·Oy loop) → DRAFT.

## 9. Phase sequencing

Phase 3 (author the six rune files + `rebuild/script.yaml` + the two schemas, ductus drafts flagged) can start immediately and runs alongside Phase 4’s three implementation groups, which begin by freezing `model.py`. Phase 5 integrates: load the real rune files through Group 1, run Groups 2–3 end to end, populate the alias map and the ledger counts, drain triage to ground, run all twelve gates. Phase 6 re-verifies byte identity and the full suite; there is no closing report — the milestone’s record is the commit history plus the runes’ `why:` fields, and the durable design facts (the semantics-coverage section, the deferred-partner list, the dead-policy explanations, the draft-ductus sign-off protocol, the registry/`script.yaml` location deviation) live in this plan.

## Deviations

### Group A (spec_load + surface)

- **Schema validation does not require `jsonschema` at runtime.** The plan routes validation through `uv run --with jsonschema`, but the module tests must run via plain `uv run pytest rebuild/ -n auto --dist worksteal` and `pyproject.toml`/`uv.lock` may not change during M1, so `jsonschema` is never importable there. `spec_load` therefore ships a small built-in evaluator driven directly by the JSON Schema files (covering exactly the keyword subset they use; an unrecognized keyword is a hard error), and `test_jsonschema_agrees_with_builtin_checker` cross-checks the two layers whenever `jsonschema` is importable — i.e. under the plan’s `uv run --with jsonschema` invocation. The schemas stay the single source of truth.
- **`SpecError` carries a list.** The contract names `SpecError(file, path, message)`; the implemented signature is `SpecError(file, path, message, line=None, issues=None)` with an `issues: tuple[SpecIssue, ...]` attribute (`SpecIssue(file, path, message, line)`), because “collects all errors before raising” needs a carrier for the collection. Single-issue construction matches the contract shape.
- **Unlock tagging rides a sibling function.** `enumerate_cells` returns plain `CellId`s per the contract; the “returned tagged with their unlock record” requirement is met by `surface.enumerate_cells_with_unlocks` (cell, unlock-records pairs) and `surface.unlocks_for_cell`. `CellPlan.unlock` (frozen by `model.py` as a single record) holds the first granting unlock when several grant one cell — qsIt’s four ss04 rows all grant the same (baseline, baseline) pairing — with the full set available from `unlocks_for_cell`.
- **E-ANCHOR lives in `surface.check_anchor_conventions`**, returning `SpecIssue` rows for Group 3’s `defects.run_gates` to merge, and it reads ink: `surface.resolved_cell_bitmap` applies live-side stub arithmetic so the dip anchors (qsPea) validate against the resolved per-cell bitmap as §3.2 requires. This is a scoped exception to the plan’s “Group 1 does not read bitmap ink” note; `withdrawal: safe` verification remains Group 3’s, via `CellPlan.safety_checks`.
- **`model.py` was frozen by a sibling group before Group A started; two narrowings were adopted rather than corrected.** (a) `Policy.groups` resolves rune-local groups to family-grain `frozenset[str]`, so trait-qualified atoms (`{family: qsDay, trait: half}` in qsIt’s `utter-pass-through-vetoes`) are widened to the bare family with a `SpecWarning`; Group 2’s matching sees families only, which is conservative for the ss04 veto carve-out and must be revisited when qsDay/qsZoo halves migrate. (b) `Condition` has no trait axis, so a trait-qualified `except:` atom is a load error (“not representable”), not silent widening. No M1 rune file hits (b).
- **`resolve:` records are rejected at load** with an explicit error: the frozen `PolicyRecord` cannot carry `against`/`at`/`pick`/`migrated`, and M1 expects zero resolves. Extending the model is a cross-group coordination event for the milestone that first needs one.
- **Unlock-added rows synthesize their anchor by convention** (entry x = min ink, exit x = max ink + 1, from the stance’s base bitmap), since the documented unlock shape carries no `x:` (authoring caveat on qsTea full’s ss02/ss03 x-height entry). A base bitmap with no ink at the unlocked height is a load error.
- **Vacuous pairings are warnings, not errors** (qsMay loop’s design-prescribed `never: {entry: baseline, exit: baseline}` names an exit the stance never declares), per the authoring caveat that `spec_load` should tolerate or drop the row. Pairings filter two-sided cells only, per §3.2’s “one-sided and isolated cells always exist”; qsIt’s `only:` rows naming `none` sides are redundant but harmless.

### Group C (geometry + defects + emit + compile + conform)

- **`model.py` carries three shared additions beyond the plan’s frozen block, documented in its docstring:** the generated adjustments-token grammar (`locked`, `en/ex-ext-N`, `en/ex-con-N`, `en/ex-trim-N`, `en/ex-bind-<bitmap>`) that settlement writes and geometry consumes; `relevant_marker_features` / `marker_glyph_name` / `locked_glyph_name`, so the table builder and the emitter agree on marker-twin and chokepoint-twin names without a cross-group import; and `CellPlan` / `GlyphRecord` as the two cross-group artifacts the plan names.
- **Generated display names: the isolated cell is the bare rune name.** The raw cmap glyph must be named exactly `qsMay`, so `geometry.isolated_cell` defines the isolated cell as (default stance, no entry, the exit whose connector ink is part of the base drawing — signaled by a `withdrawal:` binding to a named form; `safe` means the base is already withdrawn) and `display_name` names that cell bare. A cell whose exit is withdrawn relative to its stance’s base drawing gets a generated `ex-wd` part so names stay unique. Names remain never-parsed.
- **`emit_gsub` takes optional keyword inputs past the plan’s two-argument form:** `glyphs` (the realized inventory) and `isolated_cells` feed the ss10 overlay and the namer-dot follower class, and `namer_dot` names the dot/lowered glyph pair; both stages are skipped with a comment when the inputs are absent, because the contract signature cannot reach the glyph inventory. `emit_gpos` likewise takes `spec` as a keyword for the locked-twin NULL/NULL parity registrations (the rune surfaces are not reachable from the glyph mapping alone). Heights with no anchors in the supplied glyph set emit no curs lookup (the prototype shape); the real M1 build keeps all four live via qsPea (y6) and qsTea’s GPOS-parity top entry.
- **`defects.run_gates` duck-types Group 2’s tables:** `tables_by_config` values may be `(DecisionTable, TreatyTable)` pairs or single objects; treaty endpoints may be `CellId`s or string labels (resolved through an index over both generated display names and `table.cell_label` shapes), and the join attribute may be `join`/`junction` with `"break"` mapping to no-join. The extension-band check is deliberately coarse at M1 (per-record static sanity exactly; per-seam against the union of candidate bands on the pair’s runes), and dead-policy exercised-ness is provenance citation in table rules and treaty rows — both recorded as known coarseness in the module docstring.
- **The §10 tier-3 per-transition gate is realized as the exhaustive sweep plus coverage accounting:** at the 8-symbol alphabet every transition whose shortest window fits in five tokens is covered by enumeration (ZWNJ-interleaved variants included, since ZWNJ is enumerated); uncovered rules/transitions are reported in `ConformReport` and fail the gate, with BFS-derived shortest-example top-up as the growth path rather than a separate generator.
- **`compare_against_baseline` compares ligation, seams, cell identity, and — per the original §6 step 3(d) — old-vs-new positions.** An earlier deviation moved positions “to the font side”, which left the old font’s position channel ungated (the font-side sweep compares the new font against `settle()` only); that deviation is retracted. The oracle gate now shapes every seam- and ligation-identical row against the new font and diffs drawn positions (per-slot glyph origins plus the run’s total advance — the two fonts legitimately decompose a seam differently between the left glyph’s advance and the right glyph’s x_offset) against the baseline with sidecar kerns normalized out via `KernEvaluator`; uni200C is default-ignorable so the old font kerns across it, and the normalization’s kern partner skips ZWNJ slots accordingly. Rows whose matched cell-grain ledger class legitimately redraws ink are excluded and counted (`ink_identical: true` in the ledger marks the classes whose claim the position channel enforces). Ledger counts land in `BaselineReport` and `divergence-audit.tsv`; the committed-shape ledger YAML is never rewritten by the run.
- **`compile_font` uses the prototype’s `Proto`-style metadata dict under the name `AbbotsMortonSpaceportM1`** (metric parity with `glyph_data/metadata.yaml` deferred to integration) and auto-adds `space`/`uni200C` records when absent. The budget gate fails on format-3 chained-context fallback below the 16,384-byte headroom floor and yellow-flags GSUB type 7 Extension promotion in `budget.json`, per the two prototype follow-ups.
- **`coretext_smoke` copies the Swift harness into `rebuild/pipeline/`** (never importing from or invoking `prototype/`), extends the sequence set (`smoke_sequences_m1.txt`: qsPea rows, four-family seams, qsTea_qsOy windows, namer-dot rows), and runs all eight acceptance configurations per sequence.

### Group B (settle + table + explain)

- **`LeftContext` / `RightToken` live in `rebuild/pipeline/settle.py`**, not `model.py` — the plan’s frozen model block names only `Height`/`CellId`/`Settled`/`ResolvedSpec`, and `model.py` was frozen by a sibling group without the two window frames. `transition` keeps the plan’s exact signature and return type; the richer `transition_trace` (candidate table, eliminations, decided stage, joint flag, prospect) is an additive sibling that `table` and `explain` consume, and `Engine(spec, features)` is the cached form the table builder uses.
- **The withdrawn-exit cell state is encoded inside the frozen adjustments grammar.** `CellId.exit` is `Height | None` with no withdrawn token, so a mid-word declined exit whose row binds a named withdrawal bitmap settles as exit `None` plus an `ex-bind-<bitmap>` adjustment (the explicit `cells:` composition for `(entry-state, height-withdrawn)` overrides the row binding); `withdrawal: safe` rows collapse to the plain exit-none cell, and at a boundary the exit was never declined so no token is emitted. Geometry’s `bind` op applies the same substitution; integration must make sure `surface.resolve_cell`’s own withdrawal-binding resolution and the token are treated as one binding, not stacked.
- **Boundary-conditioned reachability makes the fixpoint window-exact:** a settled left state is enqueued only against the `right1` that was the producing window’s `right2`, because an entry refusal or unlock conditioned on the follower (qsTea’s half x-height entry refused before qsTea, under ss03) makes other combinations contradictory — the naive prototype enumeration produced unreachable windows that raise `E-STRANDED`. Formation-impossible windows (an adjacent ligature pair surviving unformed) are likewise excluded.
- **ZWNJ-locked inputs enumerate under a distinct `<rune>.locked` input label** (the chokepoint twin, locked before settlement, prototype-style), which keeps each plain input’s boundary-left outcomes in one block; `locked` rides `CellId.adjustments` in the settled output. The boundary lookahead class is `(uni200C, space, periodcentered)` — the namer dot joins it because it has no join surface, while staying run-transparent for word position.
- **`build_tables(spec, features)` is per configuration with config-independent labels**; marker folding and the conflict-free assertion move to `emit_gsub` per the plan’s stage list. `assert_outcome_partition` = recomputed partition disjointness + the first-match-wins replay of every reachable transition; joint rows combine the floor-broken realization ties with the table-level optimistic-prospect-vs-settled comparison.
- **Ranking-stage interpretation pinned by tests:** `order:` (stage 4) is applied across stances before the structural floor, so a non-joining preferred stance beats another stance’s equal-join-count grounded join (qsMay before qsTea+qsIt under default); the weak lead preference (stage 6) is unreachable because the floor is total, and is documented rather than coded. Cross-rune prefer conflicts at non-nested specificity raise `E-INCOMPARABLE`, same-rune ones `E-AMBIGUOUS`; equal-demand non-nested extend overlaps are tolerated with `ok:` normalized to its `[by, by]` default before demands are compared.
- **`fixtures.py` gained one missing in-alphabet record:** qsTea’s `policy.refuse[0]` (full baseline entry refused after qsPea/qsTea/qsYe/qsHe/qsExam/qsIt/qsEat) — without it Tea·Tea, Pea·Tea, and entered-It·Tea windows joined, contradicting the authored YAML. Other fixture omissions were audited as vacuous inside the 8-symbol alphabet.
- **Authored-data findings surfaced by the settlement tests (ledger material, asserted as-authored, not “fixed”):** (a) qsMay’s grounded baseline exit is unscoped and its refusal list lacks qsTea, so May·Tea joins at the baseline (today: break) and May·May·Tea regroups; (b) qsMay’s baseline entry-extension trigger list lacks qsTea_qsOy, so ·Tea·Oy·May loses today’s en-ext-1; (c) qsIt’s baseline-exit refusal toward [qsTea, qsRoe, qsIt] is self-scoped to unentered cells, so Tea·It·It joins It·It at the baseline (today: break); (d) qsMay mid-word non-joins render pulled-back (the withdrawal binding generalizes today’s before-entryless-ligature-only behavior).

### Phase 5 (integration)

- **Chokepoint-twin labels unified on `model.locked_glyph_name`:** the table builder’s `<rune>.locked` input label never existed in the glyph stream (the emitter’s chokepoint produces `<raw>.noentry` twins), so `table.build_tables` now labels locked inputs with `locked_glyph_name` and the emitter, the raw-pipeline replay, and the glyph minting all agree. The `locked` adjustment token (and its rendering inside settled cell labels like `qsTea.half.ex-y5.locked`) is unchanged.
- **The configuration fold renames raw labels per config and orders the merged rules:** `emit_gsub._fold_rules` applies `_raw_rename_map` (rune → marker twin, `<rune>.noentry` → `<marker>.noentry` for every rune whose own unlocks the config’s sets touch) to each config’s rules before the exact-duplicate union, which is what makes the fold conflict-free; `_settle_lines` then sorts each input’s merged rules `(backtracked first, marker-lookahead before bare within each block)` — sound because marker substitution is unconditional, so a marker label and the bare label it shadows never share a stream. `conform._record_rule_hits` replays through the same rename map.
- **Boundary withdrawal semantics pinned across surface/settle/geometry:** `surface.resolve_cell` no longer applies `withdrawal:` side bindings to the token-less exit-none cell — that cell is the boundary rendering (base drawing, dangling ink and all, the prototype’s anchor_kept_at_boundary); the mid-word declined exit arrives as settlement’s `ex-bind-<bitmap>` adjustment and resolves to the bound form. A live exit at a different height still takes the withdrawal binding implicitly, which keeps the side-binding-disagreement build error intact.
- **The ss10 overlay is modeled, not skipped:** the emitter’s overlay now maps locked cells to the raw cmap glyph too (today’s ss10 renders post-ZWNJ letters bare), and the conformance comparisons transform the expected side — every letter cell renders as its rune’s anchor-free cmap glyph, every seam as a break — matching today’s ss10 baseline exactly. The namer dot lowers with ZWNJ transparent to the match, as today’s font does (baseline row 00B7:200C:E670), so the split-buffer check treats the two dot forms as one slot signature and the oracle name comparison folds `periodcentered.lowered` into `periodcentered`.
- **Two YAML authoring fixes against ground truth (both mirrored in fixtures.py and the settlement corpus):** qsTea joined qsMay’s grounded-exit refusal list (behavior ground truth: today May·Tea breaks while May·May joins; the off-anchor contact gate independently rejected the join), and qsTea_qsOy joined qsMay’s baseline entry-extension trigger list (geometry ground truth: the old pipeline’s ligature expansion included it and the baseline shows today’s en-ext-1). The remaining authored-data findings stay as-authored and are ledgered (entered-It baseline joins, the generalized qsMay withdrawal, the halves entry extensions).
- **Off-anchor-contact appeals live in `rebuild/m1-contact-allow.yaml`** (committed-shape, human-reviewed like the ledger): 20 signatures, each a corner today’s font already draws on a baseline-proven join (·Oy/·TeaOy tails against ·It/·Tea bars at y1, the ·Pea·Pea y6 chain at y7, and their entered/locked variants).
- **The divergence ledger matches through one classifier:** `conform.classify_divergence` assigns each divergent row a single class from its phenomenon set (per-position alias-vs-settled cell deltas plus seam gains/losses), and every ledger predicate is `classify(row) == id`, so the exactly-one invariant holds by construction. Fourteen reviewed entries (the plan’s expected classes plus `zwnj-follower-exit-restored`, `pre-ligature-cleanup-regularized`, `bare-name-live-join`, `dangling-anchor-dropped`, and the 7-row `may-quad-order-deferral`, the one observed cost of window-local ranking — flagged for a yielding prefer at qsMay’s next policy pass). `structural-floor-drift` from the plan’s draft list landed as the sharper `regrouping-floor-drift` (gain+loss rows only); `ss03-zwnj-leak-fixed` retired — the baseline seam was already a break, so those rows surface as cell-grain members of the locked/withdrawal classes.
- **`rebuild/pipeline/run_m1.py` is the Phase 5 driver** (`uv run python -m rebuild.pipeline.run_m1`): tables + TSVs, glyph minting (settled cells named by `settle.cell_label`, so rules and glyphs agree by construction; raw cmap glyphs carry no curs anchors), defect gates merged with `surface.check_anchor_conventions`, emit, mini-font build, then the oracle gate. Font-side conformance runs via `run_m1 --conform-only` (per-config sharding with `--jobs`, horizon via `--conform-horizon`; the artifact cycle wires it in as `gate:conform`), and the CoreText smoke via `python -m rebuild.pipeline.coretext_smoke`.
