# Rebuild design: stance surfaces, riders, and the settled pipeline

This is the binding design for the rebuild whose requirements live in [core-idea.md](core-idea.md). Core-idea records _what the system is for_; this document records _what the YAML looks like and what the code does_. It was produced by surveying the current system exhaustively, generating four independent candidate designs (join-surface-first, rider-conditions, negotiation-protocol, and a conservative-reform control), scoring them with a three-judge panel against core-idea’s requirements, and then adversarially verifying the synthesized result with six specialist attackers whose confirmed findings are folded in below. The skeleton is the join-surface-first design, with specific mechanisms grafted from the other three where a judge identified them as strictly better.

## 0. Two findings that frame everything

**The convergence finding.** All four designs — written independently, from deliberately different angles — arrived at the same architecture: form ligatures first, compute stance selection in a small pure-Python settlement function over a `[resolved-left, self, raw-right, raw-right²]` window, and emit only final, positive substitutions to OpenType. Four per-height `curs` lookups, class-based pair kerning, specificity precedence with refuse-to-guess on incomparable conflicts, and recorded tie-breaks promotable to named case-groups also appeared in all four. This convergent core is settled; everything contested was at the authoring-surface layer.

**The control finding.** The conservative-reform candidate existed to answer “what is the smallest evolution of today’s format that meets the requirements?” Its honest conclusion: even the smallest such evolution requires the upstream Python resolver and the death of the emit-then-repair execution model, because the demote oracles, guard walls, name-healing layer, and `_PENDING_BK_ENTRY_GUARDS` provably cannot be rehoused in place (the derived-demote feasibility study and the leak-cleanup history each document a failed attempt). So the choice was never “reform vs. rebuild” — only which vocabulary to author the rebuild in, and conservatism buys surface familiarity while preserving the grooves the original accretion ran in.

## 1. The architecture: settle in Python, transcribe to OpenType

Today the meaning of the YAML is whatever the emitted GSUB cascade happens to do, and the pipeline simulates that cascade three times (ignore-guards to pre-empt it, demote tables to repair it, the leak contract to detect its failures) without ever computing it. The rebuild inverts this:

- A pure-Python **settlement function** is the semantics. Given a rune sequence and an active stylistic-set configuration, it deterministically computes ligation, the stance and join-surface cell of every position, every join height, and every extension amount — locally, left to right, explainably.
- The FEA emitter is a **transcription** of the settlement function’s decision table. Every emitted rule is a final, positive substitution whose context is already settled when the rule runs. Nothing is ever committed and later repaired, because “later” rules do not exist.
- Conformance is **empirical**: the compiled font is shaped through HarfBuzz over a generated set of example sequences and compared against the settlement function, transition by transition. Static analysis of emitted FEA is a proven dead end and is never attempted.

This gives “broken” the clean two-part definition core-idea asks for, with blame separation built in: a **spec defect** is the settlement function’s own output violating a structural invariant (checkable in pure Python against bitmaps and anchors, exhaustively, with no font built); a **compiler defect** is HarfBuzz disagreeing with the settlement function (always a pipeline bug, never an authoring problem).

What ceases to exist, as a category: `restore_isolated_form_overrides`, `predecessor_demote_overrides`, `trailing_demote_overrides` (172 rows), `_PENDING_BK_ENTRY_GUARDS`, the three verbatim `calt_pred_demote` passes (14,373 statements, 55% of today’s calt), the 7,327 `ignore sub` guard walls, `calt_cycle`, the post-liga repair layer, the reflip layer, the `.noentry` shadow-glyph universe (216 glyphs), the name-healing layer, `strip_entry_before`, `terminal_default`, `preferred_over`, `reverse_upgrade_from`, `extend_exit_when_entered`, `noentry_after`, and file-scope context sets. Each is either obviated by settled emission or re-expressed as one of the small number of concepts below. (One honest exception discovered in verification: a handful of follower-preference families — ·He is the worked case — carry genuine taste content that today lives in complement lists; §5.9 shows the cell-grain preference that re-expresses it, and where an author prefers the literal list, it migrates as a refuse with a `why:`.)

## 2. File layout

One file per rune — the literal form of core-idea’s “one rune per editor pane, two panes.”

```text
glyph_data/
  runes/qsPea.yaml … qsOoze.yaml      one file per character, ligature, angle paren, and space, in code-point order
  script.yaml                          registries only, never policy: heights, boundary tokens, feature registry, predicate classes
  senior_quikscript_kerning.yaml       the flat kerning sidecar (the one sanctioned locality exception)
```

`script.yaml` holds:

- The closed height table (`baseline: 0`, `x-height: 5`, `y6: 6`, `top: 8`) — adding a height is a language change.
- The boundary tokens (`space`, `zwnj`, `namer-dot`), each declaring whether it splits runs for word-position derivation (`space` and `zwnj` do; `namer-dot` is addressable as a condition value but does not split — see §3.4).
- The stylistic-set registry: every `ssNN` declares a user-facing description and a `kind: capability | taste` (the dual nature of stylistic sets as first-class data), plus declared interactions between sets.
- **Predicate classes** — the only sanctioned cross-rune sets, and they are derived, never hand-membered: each is defined exclusively by a computable expression over surfaces and bitmap geometry (`can_enter_at: baseline`, `can_exit_at: x-height`, `trait: half`, `height_class: tall`, `stroke_at: {exit: vertical}`), composable with union/minus and family-literal carve-outs. They are referenceable from any condition (§3.4), their membership is a generated report, and a hand-maintained cross-rune membership list is unwritable by schema. This is what keeps today’s most-shared sets (`halves_exit_xheight` is referenced from ~24 families; `talls` drives ·Thaw’s entry refusal) from degrading into per-rune copy-paste — and it answers the TODO’s standing demand for named letter classes declared once.

Anything that was really one rune’s private trigger list lives in that rune’s `policy.groups`. A linter flags rune-local groups whose membership is identical across two or more rune files as candidates for a predicate class.

Two-place locality holds with no third place: to understand a pair, open the two rune files. Understanding what a _derived_ class contains is a generated-report lookup, not a hunt through hand-maintained membership lists.

## 3. The rune file

```yaml
rune: qsMay
codepoint: 0xE665            # ligatures declare `sequence: [qsOut, qsTea]` instead
ductus:                      # the closed enumeration of motions; gates the stance set
  loop: |
    Written clockwise from the leftmost pixel at the baseline, looping under the baseline, ending at the x-height on the right.
  grounded-loop: |
    As the loop, but the final stroke stays down and rests on the baseline at the right.
  counterclockwise:
    unrealized: true         # enumerated but not yet drawn — the honest closed-set ledger
notes: |                     # optional prose; never load-bearing (join constraints are pairings data, not sentences)
mono: {bitmap: [...], y_offset: -3}
stances: { ... }             # §3.1–3.2
policy: { ... }              # §3.3
```

### 3.1 Stances and ductus parity

A stance is one genuine motion the hand makes to write the rune: a `motion:` reference into the ductus, exactly one hand-drawn base bitmap, and a complete join surface. Stance IDs name the pen motion (`loop`, `half`, `flipped`, `grounded-loop`), never a neighbor, boundary, or feature — the linter rejects IDs matching `(before|after|noentry|noexit|nonjoining|ss[0-9])`. A rune with exactly one stance is the sole exception: its stance and its one ductus motion are both named `hapax` (a reserved sentinel meaning “occurs exactly once”), the `motion:` pointing at that lone `hapax` key; the pen-motion label (`loop`, `bar`, …) survives only in the ductus prose. The linter enforces both — a lone stance under any other name, a single-motion ductus under any other key, or `hapax` appearing among two or more stances or two or more motions, is a spec defect.

```yaml
stances:
  loop:
    motion: loop             # build error if missing or dangling; every non-unrealized motion must have a stance
    traits: [ ]              # `half` / `alt` survive for data-expect compatibility
    bitmap:                  # the isolated drawing; double-quoted rows, trailing # markers at y=5 and y=0, as today
    - "   ##"  #
    - "  #  "
    - "  #  "
    - " #   "
    - " #   "
    - "#### "  #
    - " #  #"
    - " #  #"
    - "  ## "
    y_offset: -3
    bitmaps:                 # optional named hand-drawn siblings, referenced by cell/anchor bindings below
      pulled-back:          {bitmap: [...], y_offset: -3}
      pulled-back-grounded: {bitmap: [...], y_offset: -3}
    surface: { ... }
```

**Ductus parity** is the closed-set property made mechanical: every stance names a motion, every realized motion has a stance, and `unrealized: true` keeps an enumerated-but-undrawn motion visible until its bitmap exists. A rune cannot be authored in this format until its ductus is written. Note an honest deviation from core-idea, recorded in §15: core-idea says “finish writing all of it before starting the rewrite,” and this design deliberately resequences that global front-load into a per-rune gate — the dependency is enforced mechanically at the granularity where it binds (a rune’s migration blocks on its own ductus), because unenforced front-loaded prose is exactly what produced today’s rotten entries (qsPea’s ductus trails off mid-sentence; qsMay’s omits the counterclockwise motion core-idea itself names). This resequencing needs the author’s sign-off.

### 3.2 The surface

The surface is the heart of the format: the stance’s complete join capability, as data.

```yaml
surface:
  entries:
    baseline:  {x: 0, stroke: horizontal}
    x-height:  {x: 3, stroke: horizontal, joined: pulled-back}
  exits:
    x-height:  {x: 5, stroke: horizontal, withdrawal: pulled-back}
  pairings:
    never:
      - {entry: baseline, exit: baseline}     # qsMay's prose note, now machine-checked
      - {entry: x-height, exit: x-height}
  cells:
    - {entry: x-height, exit: x-height-withdrawn, bitmap: pulled-back}        # explicit per-cell binding when side bindings compose
    - {entry: x-height, exit: baseline, bitmap: pulled-back-grounded, entry_x: 2}   # (illustrative; ·May's real baseline exit lives on grounded-loop)
  unlocks: []                                 # ss capability rows; each may carry a narrowing when:
  require: []                                 # [entry] / [exit] for join-born stances that only exist joined
```

Key by key:

- **`entries` / `exits`** — maps keyed by height name. `x:` is the anchor’s x pixel; y is implied by the height. The validator enforces the standing conventions (`entry.x = min_ink_x_at_entry_y`, `exit.x = max_ink_x_at_exit_y + 1`) against the **resolved per-cell bitmap** (see below), and requires an explicit `x_off_convention: true` flag on the deliberate exceptions (·He, ·Ye, the inset wide-letter entries), so drift cannot be committed silently. `ink_y:` carries today’s `exit_ink_y`; `selectable: false` carries today’s `entry_curs_only` (both kept as flagged oddities). A stance may declare several entries (·Roe’s dual-height entry is two rows, not a special list form).
- **`stroke:`** — `horizontal | vertical | diagonal`, the stroke orientation at the attachment: closed condition axis 4, new first-class data, the input to the orientation-mismatch and collision flaggers.
- **`toward:` / `from:`** — optional positive neighbor scope on an exit/entry row: a row with `toward: [{family: qsMay}]` produces joining cells only against the named set (conditions from §3.4, predicate classes allowed). This is the one-line home of today’s positive allowlists (·They’s tuck exists only toward ·May; ·Roe’s baseline exit fires only before nine named families), and it is load-bearing for migration (§13.3): without it, every allowlist would have to be inverted into complement refusals, and join-count would flip hundreds of deliberately-unjoined pairs to joined. The dead-policy gate checks scopes, so an empty scope cannot rot silently.
- **`stub:`** — columns of ink at this anchor’s row that exist only in the states where this side’s join is absent, blanked in the states where the declared removal applies. A stub declares which liveness state removes it (`stub: {cols: [4], when: joined}` or `when: withdrawn`), because the real data has both polarities: ·Gay’s opened top loses its corner pixel when the entry joins, while ·May’s exit connector at column 4 is kept when the exit joins and removed when the exit is declined. Stub edits are strictly same-row and are validated against the resolved per-cell bitmap; anything that wants to differ across rows is a hand-drawn shape, never a computed edit.
- **`joined:` / `withdrawal:`** — per-side bindings to hand-drawn sibling bitmaps: `joined:` names the form used when this side is live (for redraws beyond a same-row stub), `withdrawal:` names the form used when this side is declined mid-word, or `safe` when the compiler can verify the isolated ink has no reaching connector at this row. A declined side whose ink would dangle and which binds no withdrawal shape is the hard error `E-DANGLE`. This replaces the entire authored `.ex-noentry` / `.noentry` / `noexit` universe with one checkable obligation: ·May’s x-height exit binds `withdrawal: pulled-back` (today’s two `ex-noentry` stances are just cells), and nothing is named after a context.
- **`cells:`** — the explicit binding grain. Side bindings (`stub`, `joined`, `withdrawal`) are validated sugar that expand into per-cell bitmap bindings; when two side bindings touch the same reachable cell (·May’s entry-live + exit-withdrawn cell, or a both-sides-live cell like today’s `pulled_back…and_exits_at_baseline`), the composition must be named explicitly here — a `cells:` row binds the cell to one hand-drawn bitmap, with optional per-cell anchor overrides (`entry_x:`, `exit_x:`) for the real cases where the anchor moves with the form (qsMay’s x-height entry is x=3 entry-only but x=2 in the combined cell; qsUtter.alt’s exit shifts when its reach-back entry form is active). Resolution order: explicit cell binding > side bindings > base bitmap; two side bindings disagreeing on a reachable cell with no explicit `cells:` row is a build error naming the cell. All validators (stub rows, withdrawal safety, anchor conventions, gap arithmetic) run against the resolved per-cell bitmap.
- **`pairings`** — constraints over two-sided cells: `never:` (subtractive, common) or `only:` (when the legal set is smaller than its complement, as for ·It). One-sided and isolated cells always exist unless `require` removes them.
- **`unlocks`** — stylistic-set capability rows adding an entry, an exit, or a pairing under a feature, each with an optional narrowing `when:` from the closed vocabulary — necessary, not optional, in practice: every one of today’s ten SS unlocks is context-scoped (ss05’s ·Tea both-baseline fires only after ·Et; ss02 only after ·I), and an unlock without its context would silently widen behavior into don’t-care drift. A deliberately context-free unlock is the special case, not the default.
- **`require`** — this stance only makes sense joined on the named side(s). ·Fee’s counterclockwise loop declares `require: [entry]` — and the discriminator against the binding mechanisms above is ductus: ·Fee’s joined form is a genuinely different pen motion (it loops the opposite way), so it is a stance with `require`, whereas ·Gay’s opened top is the same motion minus a starting flick, so it is a binding on the arch stance’s entry row. Only a stance may carry a requirement, per core-idea.

**Cells.** A stance’s cells are `(entry-state, exit-state)` pairs over declared rows ∪ {none}, filtered by `pairings`, `unlocks`, `require`, and row scopes. The compiler mints one glyph per cell _reachable under settlement_. The cell is the unit everything else talks about: selection picks cells, extensions adjust cells, kerning keys on cells, defect detection enumerates cell pairs. Identity is the structured tuple `(rune, stance, cell, adjustments)`; display names (`qsMay.loop.en-y0.ex-ext-1`) are generated, capped at 63 bytes with hash overflow, and **never parsed or referenced by authored data** — the name-healing layer has nothing to heal. Context tokens cannot appear in a glyph name because the grammar has no syntax for them.

**The derived view.** `make surfaces` renders each rune’s complete capability matrix (entries × exits × pairings × unlocks × row scopes, per stylistic-set configuration) as a generated, diffable artifact, also embedded as a comment block atop the rune’s compiled FEA section. This is core-idea’s “join surface read off the stance set and surfaced to the author,” and it is the first thing a reader or agent consults.

### 3.3 Policy: five rider kinds

All contextual behavior rides on existing stances or the rune, in exactly five rider kinds — `refuse`, `prefer`, `extend`, `contract`, `resolve` — plus two non-rider keys, `order:` (the rune’s stance preference list) and `groups:` (rune-local named sets). None mints identity. Every rider has one grammatical shape: optional target keys (`stance:`, `cell:`, `entry:`/`exit:` with a height) plus a `when:` condition from the closed vocabulary — the bare-condition shorthand of earlier drafts is gone; `when:` is always spelled out, and the stance-target key is always `stance:` (never `use:`).

```yaml
policy:
  order: [loop, grounded-loop]    # stance preference; default = declaration order; retires name-sort and terminal_default
  refuse:
    - {when: {right: {family: qsThaw}}, why: Joined ·Way·Thaw is ugly and awkward to write by hand.}     # whole-join grain
    - {exit: baseline, when: {right: {class: can_enter_at_baseline, except: [{family: qsNo}]}}}          # surface-row grain
    - {stance: flipped, when: {left: {family: qsZoo}, right: {family: qsEt}}}                            # stance grain
  prefer:
    - {stance: flipped, when: {left: {family: [qsIt, qsVie], joined_at: baseline}}}                      # mode defaults to yields-to-joins
    - {stance: smaller-loop, mode: absolute, when: {left: {family: qsPea, joined_at: baseline}}, why: This is what the join looks like, not a taste call.}
    - {cell: {exit: baseline}, over: {entry: baseline}, why: ·He joins the follower, not the predecessor, when both baseline joins are open.}   # cell grain, §5.9
  extend:
    - {stance: loop, exit: x-height, by: 1, ok: [1, 2], when: {right: {family: [qsDay, qsFee, qsJai, qsJay, qsRoe, qsIt]}}}
    - {stance: standard, entry: baseline, by: 1, bind: after-see, when: {left: {family: qsSee, joined_at: baseline}}}   # hand-drawn-bound form (this record belongs on qsOut; shown for the grammar)
  contract:
    - {stance: loop, entry: x-height, by: 1, when: {left: {family: qsHe}}, why: ·He's bare downstroke already sits flush.}
  resolve: []                     # recorded tie-breaks and case-groups, §6.3
  groups:
    low-setups: {union: [{family: qsDay}, {family: qsExam}], minus: [{family: qsDay, trait: half}]}
```

(The block above is a composite drawn from several runes to show every grammar form in one place; no single rune carries all of these records.)

- **`refuse`** — the unilateral veto, applied before ranking, never negotiated, at three grains (whole join, one surface row toward a context, one stance in a context). This is the daily labor — saying no — at one line per no, with union/subtraction set algebra and predicate classes for bulk. A refusal authored on either rune kills the join; the derived view renders it from both runes’ matrices even though the record lives on one. Refuse and require records may not use `right.then` — they must be decidable one position to the left of the rune they constrain, which is what keeps the lookahead closure (§6.1) inside the window.
- **`prefer`** — ranking among surviving candidates, at stance grain or **cell grain** (`cell: {…}, over: {…}` — needed wherever one stance’s cells compete, §5.9). `mode: yields-to-joins` (the default) suspends the preference whenever candidates’ window join-counts differ — core-idea’s “prefer X in isolation, yield when Y buys a better neighbor join” as the _default semantics_ of every preference. `mode: absolute` outranks join-count and is the rare, explicit escape (the format’s answer to taste-over-join cases); the linter notes that an absolute prefer is nearly always better written as a refuse. Prefers from **both** runes of a seam participate in that seam’s ranking (§6.1).
- **`extend` / `contract`** — the parametric connector layer. The target `(stance, side, height)` is mandatory; a rune-level shorthand is an error when more than one stance offers the named side and height (refuse-to-guess applied to scoping — together with mandatory explicit targets, this structurally kills the mis-scoped-extension bug class). `by:` is the author’s amount; `ok: [lo, hi]` is the authored tolerance band (the autonomous loop’s declared resting place; defaults to `[by, by]` with a global `band_slack` knob). `split:` lets one logical extension be carried partly by each side, validated against the summed band. `trim: N` (receiver-side same-row ink blanking, today’s `en-trim`) is a contract option. **`bind:`** names a hand-drawn sibling bitmap (with optional anchor overrides) in place of same-row arithmetic — the sanctioned home for neighbor-conditioned junction ink that is not mechanical connector lengthening: qsRoe’s `shortened_top`/`shortened_bottom` entry-extension forms, qsOut’s after-·See bodies, qsMay’s after-·Fee stubless entry form. Bound shapes count in the §4 watchdog. At most one extend and one contract record apply per (seam, side) — the most specific (§6.2) — and same-side records never sum; only `split:` combines sides. Different amounts for different followers are additional records on the same stance, never sibling stances.
- **`resolve`** — recorded resolutions of conflicts, in two shapes: against a named record (`{against: {rune: qsGay, id: plant-word-initial}, when: …, pick: …, why: …}`) and against the structural floor (`{at: {right: {family: qsX}}, pick: …, why: …}` — overriding the deterministic default of §6.1 for a named window class). Case-group promotion uses set expressions with union and subtraction; the subsumption linter flags individual resolves a promoted group makes deletable, and a group overlapping a surviving individual resolve is a build error. `why:` is mandatory on every resolve and every `mode: absolute` prefer; encouraged everywhere.
- **`groups`** — rune-local named sets with first-class union and subtraction over family literals, traits, and predicate classes.

### 3.4 Conditions: the closed vocabulary

A `when:` object admits exactly these keys; the schema is `additionalProperties: false`, so an eighth axis is a deliberate language change.

```yaml
when:
  left:                        # the RESOLVED left neighbor — settlement runs left to right, so this is settled fact
    family: [qsIt, qsVie]      # axis 1; ligature runes are ordinary values here; group/class references legal
    class: can_exit_at_baseline    # a predicate class (§2) or rune-local group
    stance: flipped            # axis 2 — the neighbor's resolved stance
    joined_at: baseline        # axis 3 — the height of the join being decided (none = the seam did not join)
    stroke: vertical           # axis 4 — orientation at the facing attachment
    is: boundary               # axis 6 — boundary | space | zwnj | namer-dot (see below)
    except: [{family: qsIng}]  # negation/carve-out, legal in every condition object
  right:                       # the RAW right neighbor — static facts only, never resolved state
    family: [qsDay]
    class: can_enter_at_baseline
    stroke: horizontal
    is: boundary
    except: [{family: qsMay}]
    then: {family: [qsExam], class: can_enter_at_xheight}    # a static hop; may nest two further thens, and an except entry may carry its own chain — capped three hops out
  self: {entry: live}          # this position's own cell state — live | none per side (replaces extend_exit_when_entered)
  word: final                  # axis 5 — initial | medial | final | isolated (derived from run-splitting boundaries)
  feature: ss04                # axis 7 — active stylistic set(s)
```

Boundary semantics, stated precisely: `boundary` is the disjunction — edge of run ∪ any registered boundary token. `space`, `zwnj`, and `namer-dot` are its refinements. `space` and `zwnj` split runs and derive word position (`initial` ⇔ the left context is an edge, space, or ZWNJ); `namer-dot` does not split runs but is addressable as `is: namer-dot` (today’s data needs the distinction: ·Excite guards against the namer dot where ·Utter deliberately does not). “Post-ZWNJ behaves word-initial” is therefore true by definition in the new model; today that alignment is maintained by hand and is incomplete, so it is recorded as an intended-equivalence assertion checked against the migration baseline (§13.1), surfacing divergences as triage rows rather than silent changes.

The direction asymmetry is the depth bound, by construction: `left` may reference resolved state because the left neighbor is already settled (and transitively summarizes all deeper left context — the system’s only memory); `right` may reference only static capability plus a static `then:` chain — a `then` may nest two further `then`s, and an `except:` entry inside a right condition may carry its own chain walking the same raw slots, each hop reading one more raw token, with spec_load capping every chain at three hops past the immediate right neighbor. There is no `left2`, no `right.stance`, no `right.offers`: forward viability is the settlement algorithm’s built-in lookahead, never an authorable predicate. The grammar’s words stop at the fourth raw token, which is what makes depth-4 verification complete for the rules that can exist — and only a rune’s own `prefer` records are ever handed those third and fourth tokens: refuse and require stay decidable one position to the left (§3.3), and the engine keeps those slots unknown-optimistic for closure, prospect, refusals, unlocks, and follower votes, so everything but an own-rune prefer still lives inside the depth-2 window. This widening from the original depth-2 bound is the reviewed language change §15 held in reserve — the orphaned-·Tea windows (·Day/·Oy·Tea·Utter·Low, 2026-07) needed the predecessor’s yield to see the ·Low that makes qsUtter veto ·Tea’s entry, a fact no record-level scope could express. Depth-4 followed the same way: the mid-word ·Utter orphans in ·Day·Tea·Utter·Tea·X needed the yield to read the fourth raw token, and taking it grew the table half to match the grammar — a fourth slot in each Transition/Rule, right4 enumeration in build_tables, a look4 emission in the transducer, and right4-aware conform replay and witnesses — with an import-time arity guard in table.py asserting the Transition/Rule slot count against RIGHT_WINDOW_SLOTS and RIGHT_CHAIN_CAP derived from it, so a cap raise without the matching table widening fails loudly instead of silently baking records past the window. If a genuine case ever demands resolved-right or reach past the fourth token, that is again a schema change with a design review — deliberately expensive.

## 4. The stance-vs-accretion line

Core-idea names this the hinge of the whole rebuild. The line, crisply: **a stance is a motion the hand makes to write the rune; everything that varies with the neighbors instead of with the hand belongs to the surface, the parametric layer, or policy.** Four checks enforce it structurally:

1. **Ductus parity** — every stance realizes a named motion; every realized motion has a stance. You cannot mint a stance without first writing down the motion it claims to be.
2. **One unique hand-drawn bitmap** — the comparison runs anchor-normalized over _all_ of a rune’s hand-drawn bitmaps, `bitmaps:` siblings included: two drawings whose ink differs only by connector/stub pixels at declared attachment rows are a build error naming the mechanism that already expresses the difference (“this is a surface row / an extension / a stub”), which keeps the bound-shape channel from going free-form.
3. **Context-free identity** — stance IDs and data contain no neighbor, boundary, or feature reference (linted); triggering context lives in policy records that _reference_ the stance.
4. **Complete in itself** — the surface states everything the stance can do; reading the stance answers “what can this motion do?” with no residue outside the rune’s own policy block.

For migration triage, the five-step decision procedure (from the reform design) is the rubric for any existing record: differs only in which neighbors summon it → a `prefer`/`refuse`/row scope; differs only in which anchors are live → a cell; ink differs only as join-localized consequence of an anchor being live or declined → a `stub`/`joined`/`withdrawal`/`cells:` binding; ink differs as reach toward one specific neighbor → an `extend` (parametric, or `bind:`-bound when the redraw exceeds connector arithmetic); otherwise the pen moves differently → a stance, named after the motion. The step-3/step-5 discriminator is ductus: a join-summoned redraw of the same motion is a binding; a different motion (·Fee’s reversed loop) is a stance.

Applied to today’s census of 114 stances: 36 SHAPE + 1 TASTE stances survive (context stripped from their names); 33 ANCHOR stances become cells; 13 SUPPRESS become refusals; 10 CONTEXT become prefers, row scopes, or nothing at all (most are emergent — see ·No below); 10 SS become unlock rows with their contexts intact; 11 EXT become extend records (parametric or `bind:`-bound). The recast readability test survives: entries may be long, but every line is one of a fixed set of explainable kinds, scatter is impossible (there is no third place), and `why:` one-liners attack mystery where structure alone cannot.

Because the soft channels are where accretion could re-grow, the derived view surfaces per-rune counts of prefers, bound junction shapes, refusals, and **resolves** (total and `migrated:`-provenance separately, so the bulk-accepted migration ledger is a visible debt number the subsumption linter is expected to drain), and the linter flags pileups — visibility guarantees layered on top of the hard structural ones.

## 5. Worked examples

All geometry below is today’s real data. (The four candidate designs each work all twelve of core-idea’s hard cases in their own syntax; these are the synthesized format’s load-bearing ones.)

### 5.1 ·May: motions, and surrender with one authored line

·May is two realized motions (`loop` rising to the x-height; `grounded-loop` resting on the baseline) plus an `unrealized: counterclockwise` ledger entry. The real geometry, expressed exactly:

```yaml
stances:
  loop:
    motion: loop
    bitmap: [...]                          # today's mono drawing
    bitmaps:
      pulled-back: {bitmap: [...]}         # today's pulled_back_a_bit_for_entry_at_short_height — one pixel off at y=5
      pulled-back-stubless: {bitmap: [...]}    # today's …without_stubbie, the after-·Fee entry form
    surface:
      entries:
        baseline: {x: 0, stroke: horizontal}
        x-height: {x: 3, stroke: horizontal, joined: pulled-back}
      exits:
        x-height: {x: 5, stroke: horizontal, withdrawal: pulled-back}
      pairings:
        never: [{entry: baseline, exit: baseline}, {entry: x-height, exit: x-height}]
      cells:
        - {entry: x-height, exit: x-height-withdrawn, bitmap: pulled-back}   # entry-live + exit-declined compose to one drawing
  grounded-loop:
    motion: grounded-loop
    bitmap: [...]                          # today's exits_at_baseline
    bitmaps:
      pulled-back-grounded: {bitmap: [...]}    # today's pulled_back…and_exits_at_baseline
    surface:
      entries: {x-height: {x: 3, joined: pulled-back-grounded, joined_x: 2}}     # the entry anchor moves with the bound form
      exits:   {baseline: {x: 4}}
policy:
  contract:
    - {stance: loop, entry: x-height, bind: pulled-back-stubless, when: {left: {family: qsFee, joined_at: x-height}}, why: ·Fee's long reach-over absorbs the baseline stubbie; the redraw spans rows, so it is a bound shape, not arithmetic.}
```

The connector pixel at column 4 of the y=5 row is kept when the exit joins (it is the connecting stroke) and removed when the exit is declined mid-word — so it is the `withdrawal: pulled-back` binding, _not_ a stub blanked on join, and `withdrawal: safe` would rightly fail `E-DANGLE` verification here. In `·May·They+Utter` — where the ligature refuses entries after ·May — settlement sees before committing anything that the seam offers no entry, so ·May lands in its (baseline-entry, exit-withdrawn) or (none, exit-withdrawn) cell, rendered with the bound form. Today’s two authored `ex-noentry` stances, the `_exit_noentry_fallback` scorer, and the post-liga left-cleanup pass are all replaced by cell semantics plus one `withdrawal:` binding; the four hand-drawn ·May variants of one motion all survive as bindings of one stance, and nothing is named after a context.

### 5.2 ·No after ·It/·Vie: the canonical accretion stance, dissolved

·No is two stances: `loop` (x-height in/out) and `flipped` (`traits: [alt]`, baseline in/out). There is no rule for the ·It/·Vie case at all: ·It’s `only:` pairings say entered-at-x-height ⇒ exits-baseline, so when ·It resolves with a baseline exit, the only ·No candidate with a matching entry is `flipped`, and capability matching does the work. The condition “·It and ·Vie exit at the baseline only sometimes” is not written anywhere because it is the definition of axis 2: settlement reads the neighbor’s resolved choice. `qsNo.alt_after_it_and_vie` existed only because the old format had no such read. Where an author _does_ want to write the resolved fact (a kern, a refusal scope), the atom is `when: {left: {family: qsIt, joined_at: baseline}}`.

### 5.3 ·It·No: contextual preference that yields

One record on qsNo: `- {stance: flipped, when: {left: {family: qsIt}}}` (default mode, yields-to-joins). Settlement at ·It’s seat ranks pair candidates (§6.1), with ·No’s prefer participating from the right side of the seam. In isolation, both seam heights score 1 join with no onward prospect, so the tie goes to the prefer: flipped wins. In `·It·No·Owe`, the x-height path scores 2 (the seam plus ·No’s prospective x-height exit into ·Owe, which the baseline path forecloses), so plain `loop` wins before the prefer is consulted. In `·Roe·It·No` with ·Roe resolved to a baseline exit, ·It’s pairings force the x-height exit and only `loop` can join — capability, not preference. One line, three behaviors, each printable by `tools/explain.py`. (A scope note from verification: ·Roe’s baseline exit is itself allowlist-scoped today — its row carries `toward:` the nine families it serves, which does not include ·It; the example’s premise holds via ·Roe’s other followers.)

### 5.4 ·Utter·Gay·Low·It (“ugly”): priority over the intersection

·Gay’s (x-height-entry, baseline-exit) cell — its entry binding the hand-drawn opened-top bitmap — is the only candidate whose entry matches ·Utter’s resolved x-height exit; it joins ·Low’s baseline entry, beating the exit-declined cell on window join-count. Settlement evaluates candidates against both neighbors at once — literally “the highest-ranked member of (left-compatible ∩ right-compatible)” — and the decision-table builder flags such rows `joint`, which is what routes them to the expensive cross-product test tier and makes the cost legible.

### 5.5 ·Tea: the stylistic-set capability matrix

·Tea’s `full` stance declares baseline and top entries, a baseline exit, `never: [{entry: baseline, exit: baseline}]`, and context-scoped unlocks: `{pairing: {entry: baseline, exit: baseline}, feature: ss05, when: {left: {family: qsEt}}}` and `{entry: x-height, feature: ss02, when: {left: {family: qsI}}}` — the contexts migrate with the unlocks, because silently widening ss05 from “after ·Et” to “after any baseline exit” would land entirely in don’t-care drift. No stance is named after a set; `make surfaces` renders the matrix per configuration, which is the read-off artifact (“what is allowed is a function of (left, right, active sets)”). The Manual’s one ss05 use is held by its data-expect pin under `data-stylistic-set="05"`.

### 5.6 ·Way·Thaw: the unilateral veto

One rune-level line on the lead: `refuse: [{when: {right: {family: qsThaw}}, why: ...}]`. Nothing compiles — settlement never produces a joining candidate for the pair, so no FEA rule ever proposes one. Negative space costs zero `ignore sub` statements.

### 5.7 ·Out+Tea, the ·Excite·Tea·Oy guard, and ligature formation

`qsOut_qsTea` is a rune (`sequence: [qsOut, qsTea]`) with its own ductus, stances, and (empty) surface; `qsTea_qsOy` has an exit and no entries. Because formation runs before stance selection, ·Excite’s widened baseline-exit motion (whose row carries `require`-like scoping toward baseline enterers) sees the entryless ligature as its right neighbor, has no serviceable candidate, and resolves to a plain cell — keeping whatever left join it already made. The `_PENDING_BK_ENTRY_GUARDS` table and the `qsExcite.*.before-vertical` stance family fall out of “a declared join must physically realize” applied _before_ commitment.

Formation semantics, stated precisely after verification and revised when the qsLow migration produced the live counterexample (·Day·Utter·Low — the Manual pin `·Day | ·Utter.alt ·Low`, where the ligature exits only at the x-height, ·Low enters only at the baseline, and forming would destroy the seam the unformed alternate ·Utter carries): in the model, **formation is a settlement decision** like any other — a _guarded_ one. A ligature yields to its components per window exactly when the trailing component, left unformed, could realize a seam toward the follower that the formed ligature could realize under no capability configuration (`settle.formation_blocked`: refusal-aware at candidacy grain, quantified over the powerset of unlock features because formation stages before the ss markers and is therefore config-blind by design; its two slots are the raw tokens after the sequence — the same slots the emitted lookup reads — so the guard never depends on state formation cannot see). The decision happens before forming — never an un-form-and-repair pass, which the architecture forbids — and it is the shipped font’s mid-pipeline variant-keyed shape restated as a derivation: today’s `calt_liga` re-captures ·Utter’s `reaches-way-back` and `before-may` flips into `qsDay_qsUtter` but not the plain baseline-reaching alt, which is precisely “form unless the unformed trail reaches a follower the ligature cannot serve.” Because the guard derives entirely from the runes’ join surfaces, a newly migrated letter that only the unformed rendering can reach extends the guard automatically — The Manual stays satisfied by default, with no per-ligature data. Left-side seams stay exempt on purpose: formation may still cost the predecessor its join (`qsTea_qsOy`, above), exactly as the shipped font proves. Compiled shape: a ligature the guard ever blocks moves to a chaining-context lookup (`m1_formation_guarded`, first in `calt`) whose generated `ignore sub` rows carry the guard over one or two raw lookahead slots, ZWNJ-explicit forming rows ordered ahead of them; unguarded ligatures keep the plain type-4 lookup. Verdicts the staging cannot express — one that differs across capability configurations, or one that blocks at a boundary second slot without blocking everywhere — are hard emitter errors, and that is where the design bends next.

### 5.8 An incomparable conflict, recorded and promoted

Suppose ·Way’s half declares a word-final prefer (axis 5) and ·Roe declares a before-·Tea prefer (axis 1) that demand different heights at the (·Way, ·Roe) seam. Neither condition’s match set contains the other → build error `E-INCOMPARABLE` with an example sequence and a paste-ready stub. The author records a `resolve` on one of the two runes, and when the same conflict recurs with ·Why, promotes it to a case-group whose membership is a set expression with union and subtraction (word-final ·Way carved back out to keep its flourish). The subsumption linter flags individual resolves a promoted group makes deletable, and a group overlapping a surviving individual resolve is a build error — promotion _shrinks_ the pile rather than relocating it.

### 5.9 ·He: follower preference at cell grain

·He’s entry-baseline and exit-baseline forms are the same drawing, so they are cells of one stance with a `never` pairing — and today’s rule “·He joins on the left here or on the right elsewhere, never both, and prefers the follower” is enforced by a 31-family complement list. In the new format it is one cell-grain prefer on qsHe: `{cell: {exit: baseline}, over: {entry: baseline}}`. Semantics (§6.1): the record participates in the _predecessor’s_ seam ranking, voting to withhold the predecessor’s exit whenever ·He’s own forward continuation is refusal-aware realizable — decidable inside the window. In `·Day·He·Day` both assignments score one window join; ·He’s cell prefer breaks the tie toward the forward join. In `·Day·He·-ing`, ·-ing’s refusal of entries after ·He (a refuse on qsIng) makes the forward prospect score zero, so ·Day·He joins — the case a naive static-capability refuse would get wrong. The complement list dies; one line with a `why:` replaces it.

## 6. Selection semantics

### 6.1 The settlement function

Per run (boundary to boundary), after formation, left to right. At position i, the unit being ranked is the **pair candidate**: a tuple (cell of rune i, seam state toward i+1 — a height or none).

1. **Entry binding.** A committed seam is bilateral and final: if position i−1 committed an exit at height h, only entry-h cells of rune i are candidates; entry-none cells are candidates only when the left seam committed no join. The follower can never decline a committed seam — and an acceptor always exists, because step 2’s lookahead closure at position i−1 already proved one.
2. **Lookahead closure.** A pair candidate with seam height h is admissible only if some cell of rune i+1 survives **all** of: its own pairings, `require`, unlocks, row scopes, and every refuse/availability record decidable within the window — evaluated with rune i’s candidate as i+1’s resolved left and rune i+2’s raw surface as its right. (Refuse records are restricted to window-decidable conditions by §3.3, so this closure is total.) Mutuality is definitional: an exit with no refusal-aware acceptor is never a candidate, so **reaching joins are unrepresentable**. The table builder additionally asserts, as a machine-checked invariant, that every committed exit in the emitted table has at least one acceptor cell at the next position; a violation is the hard error `E-STRANDED` (with the example sequence and the records that eliminated the last acceptor) — dead ends are impossible by construction, not unaddressed.
3. **Refusals.** Any matching `refuse` on either side kills a candidate. Absolute, unranked.
4. **Ranking** — strictly lexicographic:
   1. Absolute prefers from both seam runes, most-specific first (§6.2).
   2. **Window join-count**: [left seam realized] + [own seam realized] + [best refusal-aware static prospect of the (i+1, i+2) seam given this candidate]. The third term is computed over i+1’s surviving cells against i+2’s raw surface — entirely inside the window — and is deliberately optimistic with respect to i+1’s own prefers and ordering; the table builder compares every row’s optimistic prospect against i+1’s actual settled choice and auto-flags divergent rows `joint`, routing them to the expensive test tier instead of leaving the divergence silent.
   3. Yielding prefers from both seam runes (a follower’s stance- or cell-grain prefer is evaluated per enumerated continuation, with `joined_at` bound to the candidate’s seam height), most-specific first; cross-rune prefers at equal specificity demanding different outcomes are `E-INCOMPARABLE`.
   4. The runes’ declared `order:`.
   5. **The structural floor** — a deterministic, silent default for record-free ties, so don’t-care windows never become build errors: prefer the candidate that realizes the left seam, then the lower seam height, then the surface’s row declaration order (`none` last). Record-free ties settle here and surface as treaty-diff rows — preserving core-idea’s “don’t-care is the default, discovered not declared.” `E-AMBIGUOUS` is reserved for genuine record-vs-record ties.
   6. Weak lead preference, as the final tiebreak between records of the two seam runes.
5. **Commitment.** The cell and seam state are final; settlement advances. There is no revisiting and no repair pass, because commitment is the trigger for everything downstream.

Word position falls out of run-splitting boundaries (§3.4); word-final winners are just the rows that match when the right is a boundary — `terminal_default` has no successor because nothing needs one. Withdrawal is part of candidate semantics, not a fixup: a stance’s cells form a lattice (both-sided → one-sided → isolated) and settlement simply lands on the cell the seams support, rendered with the bound bitmaps of §3.2.

### 6.2 Specificity, formally — and extensionally

A record’s specificity is computed **extensionally**: every constrained axis resolves to its concrete match set over the finite registry (families × stances × cells × heights × features × boundary values). Record A outranks B iff A’s match set is a subset of B’s on every axis B constrains, with at least one strict subset. Within one axis, narrowness is set inclusion after expansion — a literal `[qsExam]` is narrower than `[qsYe, qsExam, qsI]`, a singleton group ranks by what it denotes, and mixed literal-plus-class conditions are well-defined for free. Non-nested overlap on a shared axis with conflicting demands is `E-INCOMPARABLE`. (A syntactic kind-rank — resolved cell before stance before family — was considered and rejected: real records exist where it disagrees with extensional inclusion, and it is undefined for mixed conditions.) Nesting conflicts therefore resolve silently — the narrow single-family contract beats the broad list-authored extend by membership, today’s documented idiom as a theorem — and crossing conflicts refuse to guess, with two distinct hard errors (`E-INCOMPARABLE`, `E-AMBIGUOUS`) each demanding a recorded `resolve`. Migrated idiom pairs that land incomparable under the extensional order are auto-resolved by §13.5’s triage with `migrated:` provenance, so the change is recorded law rather than a silent rank artifact. There is no axis-priority ordering, no modifier-count sort, no name sort. Because specificity feeds the same settlement it orders, evaluation is stratified (capability first, then policy) so the computation is well-founded, and the specificity module ships with its own dedicated regression-test class — it is the one place a quiet bug would masquerade as taste regression. Two named regression cases ship with it: the decline-discriminator window (an x-height-exiting predecessor + ·It + ·They) and the qsJay contract-vs-extend overlap.

### 6.3 Explainability and the emergence compensation

The strongest legitimate criticism of this whole architecture (all three judges raised it): emergent behavior has no authored line — reading qsNo.yaml does not say “flipped fires after a baseline-exiting ·It.” Three compensations are therefore part of the design, not afterthoughts: (a) `tools/explain.py SEQUENCE [--features ssNN]` replays settlement with the full candidate table, every elimination attributed to a file/record, and the rank comparison that chose the winner — it ships with the first migrated rune; (b) every emitted FEA rule carries a provenance comment naming its decision-table row, so FEA spelunking routes back to one explainable decision; (c) the derived per-rune report cross-references policy on the _other_ rune of every pair it participates in, so the two-pane reading experience does not depend on memory, and the review tool’s one-keystroke “pin this” converts any emergent outcome the author cares about into a cheap whole-word assertion the moment it is noticed.

## 7. Compilation to OpenType

Everything below is within shapes the current 30,838-line FEA already proves reachable; the difference is that the settled function is computed first and transcribed.

| Format construct | OpenType realization |
| ---------------------------- | -------------------- |
| Cells (+ adjustments) | One compiled glyph per cell reachable under settlement; identity is the structured tuple; names generated, display-only |
| Attachment heights | Four `curs` lookups, one per height, NULLed anchors for cross-height glyphs — verbatim today’s proven encoding; height mismatch impossible at GPOS level |
| Formation | Plain GSUB type-4 over bare runes for unguarded ligatures, preceded by a chaining-context lookup carrying the generated late-formation guard rows (`ignore sub` over one or two raw lookahead slots, ZWNJ-explicit forming rows first) for any ligature `settle.formation_blocked` ever blocks (§5.7) |
| ZWNJ / boundaries | The proven chokepoint (`sub uni200C @entry-live' by @entry-locked`) plus the generated default-ignorable coverage transform — which must cover ZWNJ at **every** slot of the settlement rule shape (backtrack, first, and second lookahead), emitting the table’s boundary-outcome rows with `uni200C` explicit in the class at the boundary slot, ordered ahead of any join row that could match across a skipped ZWNJ; generated per-lookup guard statements are the sanctioned fallback if positive ordering proves insufficient at the week-one prototype. Emitter invariant: locked twins and chokepoint outputs appear in no raw lookahead class, family-keyed classes included. The “zero `ignore sub`” claim below is scoped to selection semantics; generated ZWNJ-coverage guards and the late-formation guard rows (§5.7) are exempt |
| Settlement | The decision table transcribed as chained-context single substitutions: backtrack matches _settled_ classes (GSUB backtrack sees post-substitution glyphs), lookahead matches _raw_ classes — `sub <settled-left class> <rune>' <raw class> <raw class> by <cell glyph>;`, positive rules only, zero selection-semantics `ignore sub`, classes everywhere via outcome-partition (DFA-style) compression |
| Word position | Fallback-row ordering for word-final defaults; the substitute-then-revert lookup pair kept in reserve for positive `word: final` records |
| Capability unlocks | `ssNN` marker substitutions staged **after formation and before settlement** (so enabling a set cannot un-form a ligature; ligature runes carry their own markers for unlocks they declare); settlement tables include marker glyphs as raw inputs. Runes with several applicable sets get generated composite markers (union semantics); the conformance matrix includes at least one multi-set configuration (e.g. ss02+ss03+ss05 on ·Tea) in addition to per-set runs |
| Taste sets / ss10 | Post-`calt` single-substitution overlays over resolved cells (today’s ss06/ss10 shape); ss10 auto-generated as cell → isolated cell |
| `refuse` / `prefer` / `resolve` | Not compiled at all — they shape the decision table in Python; the FEA only ever sees final substitutions |
| Extensions / stubs / trims / bound shapes | Baked into cell glyph bitmaps at mint time where possible; a small settled-pair substitution stage where the adjustment depends on the seam |
| Kerning | One class-based PairPos format-2 lookup (global row + pair rows) plus GPOS type-8 contextual kerns |
| Namer dot | The existing final mini-`calt`, unchanged |

**The de-risking prototype comes first.** The transducer encoding — one settlement lookup, optionally partitioned into per-family **subtables** for size (subtables share the lookup’s single left-to-right pass, so backtrack still sees settled neighbors) — is architecturally sound: backtrack-sees-settled _within a lookup_ is shaper-spec behavior the current font already exercises. Per-family **lookups** are unsound and are not an option: each lookup finishes the whole buffer before the next starts, so any rune whose family’s lookup runs first would see a raw left neighbor, and the cyclic left-adjacency between families means no lookup order fixes this — the only sound multi-lookup factorization is the two-strata fallback. Week one therefore builds the settlement emitter for the three worst families (qsIt, qsTea, qsMay) plus one ligature, measures compressed rule count with `_report_gsub_budget` as the kill criterion, and runs cross-shaper smoke tests (HarfBuzz, CoreText, DirectWrite) on within-lookup sequential-substitution semantics, including ZWNJ-interleaved cases. The designed fallback is the two-strata staged-wave emission (stratum 1 over raw facts, stratum 2 keyed on stratum-1 output classes — pass count provably 2, never an empirical fixpoint); note honestly that stratum-internal chains still rely on within-lookup sequencing, so the bottom fallback is per-dependency-rank waves, bounded by the longest settled chain. Either way the guard/demote/repair categories stay deleted; only emitted size is at stake. Expected scale on the primary path: ~950 curs statements unchanged, settlement in the low thousands of rules, total FEA roughly 3–6k lines against today’s 30.8k.

## 8. Generated artifacts

Three committed, generated, diff-stable artifacts make “trusting a change” a diff instead of a hunt. CI regenerates and compares, so hand-editing them cannot land.

- **The settlement table** (`build/settlement.tsv`): one row per (settled-left class, rune, raw-right window) → cell, with provenance pointers to the YAML records that shaped it. Its diff is the semantic diff of the font’s behavior — immune to the example-label churn that produced the 28-vs-1 measurement trap.
- **The treaty table** (`build/treaties.tsv`): one row per reachable adjacent cell pair — join height or break, summed extension, kern. Kerning hidden-junction discovery, defect enumeration, and the review surface all read this.
- **The capability matrices** (`build/surfaces/`): per rune, per configuration; embedded as comments atop each rune’s compiled FEA section. `build/joint-dependent.txt` lists the rows where the left choice genuinely depends on the right context — the legible register of where separability fails and the expensive test path applies.

## 9. Defect detection

Everything runs at build time against the tables plus bitmaps, before a font exists; every gate has a signature-keyed declared-OK channel in the proven force-list shape (asymmetric: new failure fails, resolved failure re-blesses).

| Defect | Mechanism | Disposition |
| ------------------------------ | --------- | ----------- |
| Reaching join, no acceptor | Unrepresentable: never a settlement candidate (refusal-aware lookahead closure, §6.1) | impossible by construction |
| Stranded commitment | Table invariant: every committed exit has a refusal-aware acceptor at the next position | `E-STRANDED`, build fails |
| Height mismatch | Cells are height-keyed; `curs` is per-height | impossible by construction |
| Selected join doesn’t realize | Per treaty row: `gap = right_ink_to_entry − left_ink_to_exit − 1` after adjustments must be 0 (today’s proven arithmetic, now over every reachable row, not a 1+1 sample) | `E-UNREALIZED`, build fails |
| Dangling withdrawn ink | Every reachable declined side checked for reaching ink against the resolved per-cell bitmap; `withdrawal: safe` verified, else a bound shape required | `E-DANGLE`, build fails |
| Off-anchor contact | Overlay the two cell bitmaps at the settled offset (curs or advance+kern) for **every** reachable join _and_ non-join adjacency; ink contact at any non-anchor row | hard error, appealable — the previously missing general detector (today only the hand-built ·It·Roe check exists) |
| Extension too short / too long | Same arithmetic against the authored `ok:` band | too short: error; beyond band: flagged |
| Collision / false thick stroke | Facing same-orientation vertical runs adjacent within ≤ 1 px over ≥ K rows, joined or not, from `stroke:` + ink columns | flagged ugly-with-signature, promotable to a refuse |
| Orientation mismatch | Treaty rows pairing orientations a small per-rune dislikes table rejects (the ·No case) | flagged only — taste stays the author’s; a flagged-ugly class can be promoted to a hard error via a per-detector severity override, which is the mechanism for core-idea’s “ugly-with-a-signature → broken invariant” promotion |
| Dead policy | Any refuse/prefer/extend/contract/resolve, row scope, or unlock satisfiable by zero reachable windows | warning, asserted empty — nothing skips silently (the lookup-ordering no-op lesson) |
| Anchor-convention drift | The x-convention validator against resolved per-cell bitmaps, a hard gate with explicit exception flags | `E-ANCHOR` |
| Cross-break leakage | At boundary tokens, settlement across the boundary equals settlement of the halves by definition. Mid-word non-join adjacencies are _not_ definitionally isolated (the left’s resolved state legitimately conditions the right), so the bad/benign leak classifier runs over the table’s break rows exactly as today, with the black-box sweep retained as trust-but-verify | classifier + belt-and-suspenders gate |

“Broken” gets its precise, recorded-quantifier definition: broken = any `E-`row, quantified over the enumerated reachable set. The autonomous fixing loop (“an agent loop you are not in”) gets a complete detector whose inner loop is a table recompile (seconds, no font), subtractive one-line levers (a refuse, a band-respecting amount, a withdrawal shape, a resolve), and gates that cannot pass while an example sequence fails.

## 10. Testing and pinning

Five tiers, cheapest first; the first two need no font.

1. **Static spec validation** — schema, ductus parity, naming lints, dead policy, anchor conventions, `E-STRANDED`/`E-INCOMPARABLE`/`E-AMBIGUOUS` and the rest of the `E-` family.
2. **The exhaustive pure-Python sweep** — every depth-2 window over the alphabet plus boundaries through the settlement function and every §9 detector, minutes, no fonts. The primary gate, and the agent’s inner loop.
3. **The per-transition conformance gate** — enumerate every decision-table transition, derive a shortest example sequence per transition (including sequences longer than 5 runes that today’s depth-4 sweep provably misses, and ZWNJ-interleaved sequences for every transition class), shape through HarfBuzz, diff against the settlement function. Coverage is per-transition, not per-depth — this converts “no fixed sweep depth is provably complete” from a permanent fear into a closed obligation. Run per stylistic-set configuration for each set alone, every configuration the Manual’s pins use, and at least one declared multi-set combination.
4. **The depth-4 black-box leak snapshot** — retained as belt-and-suspenders with its asymmetric bad gate, benign census, and force lists, because the lessons archive shows by-construction proofs keep meaning “with the mechanisms considered.”
5. **Pins** — the data-expect minilanguage and the ~603-cell Manual corpus carry over verbatim (sacred cargo): glyph tokens, `~x~/~b~/~t~/~6~`, `|` with the break-isolation invariant, `+?`/`+|`, `.∅`, `.half`/`.alt` against compiled traits, compat assertions now derived from structured cells instead of parsed names. Backward-compatible additions: an any-of connective at the corpus layer (promoting the Python-only `_assert_expect_any`), in-string stylistic-set scoping for single connections, a word-position scope marker, and an optional extension-band assertion. Whole-word assertions remain the preferred cheap lock.

Joint-dependent rows (and only those) get the full cross-product sweep treatment, sharded as today — core-idea’s “discover separability per region,” computed rather than hoped. The byte-identity discipline survives within the new emitter for pure refactors; behavioral equivalence across spec changes is the settlement-table diff.

## 11. The review surface

`make treaty-diff` against a baseline produces the review page: one row per changed settlement/treaty entry, rendered in the live font per the proven tester-page requirements (checkered background, the pair under review unmistakably highlighted, side-by-side before/after via dual `@font-face`, one-key home-row verdicts with auto-advance, per-row notes, copy-out). Verdict exports close the opinions-become-pins loop mechanically: thumbs-up drafts a whole-word data-expect pin covering the row; thumbs-down drafts the one-line refuse/extend/prefer edit naming the provenance records; “fine either way” records into the any-of channel. Don’t-care drift is surfaced by default — every behavioral change is a changed row, every changed row is rendered, in batches of hundreds — which closes the one stratum today’s tooling surfaces nowhere (a changed joined mid-word pair outside the corpus). This application is part of the work, not an afterthought; its first deliverable is the migration baseline diff (§13).

## 12. Kerning

The sidecar stays flat, `---`-separated, and textarea-editable (the one sanctioned locality exception), with structured keys instead of name prefixes:

```yaml
---
global: {value: -1}                                   # the whole-Senior 1px tighten; exactly one such record
---
left: {rune: qsNo, stance: flipped}                   # resolved-stance keying: the ·No.alt·Pea case
right: {rune: qsPea}
value: -2
---
left: {rune: qsWay}                                   # cell-grain keying: today's qsWay × qsGay.ex-y0 rule
right: {rune: qsGay, ex: baseline}
value: -1
---
left: {rune: qsHe}
right: {boundary: zwnj}                               # the contextual kern, GPOS type 8 — proven pattern
value: -3
```

Sides are structured refs — rune, optional stance, optional cell pattern (`en:`/`ex:` with a height) — resolved against the reachable-cell inventory at build time; a key matching nothing is a build error (today a stale prefix silently kerns nothing). Because GSUB fully resolves before GPOS, resolved-pair keys stay context-aware for free (`qsNo.flipped × qsTea.half` encodes the ·It that caused the half). The treaty table’s reachability data drives the hidden-junction drill-downs in `site/kerning.html` and the standing corpus probe for “one resolved pair wants two different kerns” — the machine check that would surface the contextual-kern counterexample core-idea leaves open. Compilation: one class-based PairPos format-2 lookup plus the type-8 contextual rows, replacing 268 single-rule lookups.

## 13. Migration

Gated per rune on ductus, oracled by black-box extraction, deleted-with-proof for the patch layers.

1. **Baseline extraction (build this first).** Shape the depth-2 basis through the _current_ font and record every window’s resolved outcome as a baseline table. Pure black-box shaping — zero old-pipeline archaeology — and it is simultaneously the migration oracle (“done” = the new table matches except where a reviewed row says otherwise) and the review surface’s first real workload. The intended-equivalence assertions (post-ZWNJ ≡ word-initial, §3.4) are checked against this baseline so divergences surface as triage rows.
2. **Ductus, per rune.** A rune cannot be expressed in the new format until its motions are written (`motion:` is mandatory), so the 54 missing ductus entries are written rune by rune as migration reaches them, with `unrealized: true` keeping honesty cheap. The census’s per-family bitmap/stance inventories are the worklist; every existing bitmap must land under some motion (as a stance’s base or a bound sibling) or be retired.
3. **Mechanical conversion (scripted, most records).** The census labels drive the converter: ANCHOR stances → surface rows and cells; SUPPRESS → refusals; CONTEXT → prefers, row scopes, or nothing at all (attempted as _nothing_ first — most should be emergent); SS → unlocks with their contexts intact; EXT → extend records (parametric or `bind:`-bound); `derive` directives → extend/contract records with mandatory targets. **Allowlist polarity is preserved mechanically**: every old positive `select.before`/`after` gate on an exit/entry stance becomes a `toward:`/`from:` row scope, one-to-one — without this step, join-count would flip every absent-from-allowlist pair (·Roe·It and its hundreds of siblings) from unjoined to joined, a systematic inversion, not incidental drift. Context sets → predicate classes (geometric) or rune-local groups (private); kerning keys via an alias table (`qsNo.alt → qsNo.flipped`); bitmaps, anchors, traits, ligature sequences, and the corpus HTML verbatim.
4. **Deleted on evidence, not faith.** The 172 override rows, `_PENDING_BK_ENTRY_GUARDS`, and the guard/demote/reflip passes are never converted; the baseline table proves their behaviors are preserved (or surfaces the divergence for triage, where each becomes an explicit refuse/prefer/resolve with a written reason — the “every line explainable” upgrade those rows never had).
5. **Arbitration triage.** The first full build will throw a batch of `E-INCOMPARABLE`/`E-AMBIGUOUS` where today’s outcome rests on modifier-count and emission-order accidents. The tool proposes the resolve that reproduces the baseline row with provenance `migrated: matches pre-rebuild behavior`; the author bulk-accepts and re-judges at leisure — recorded law beats silent accident. The migrated-resolve count is a visible §4 watchdog number the subsumption linter is expected to drain as case-groups emerge.
6. **Order and acceptance.** Worst accretion offenders first (qsIt, qsTea, qsPea, qsMay — 42 stances between them prove the collapse), the 28 stance-less families mechanically last. Acceptance: Manual corpus green under its recorded ss configurations, conformance gate green, treaty-diff triage drained, leak gate at or below today’s backlog, kerning hardcases re-recorded. Byte identity is explicitly not a target — the old FEA is the thing being escaped. Cutover archives the old pipeline and adds the demote-oracle and guard-table chapters to `doc/graveyard/`.

## 14. Pipeline modules

Small, individually testable passes from day one (the 4,200-line-monolith lesson), each with a dumpable artifact between stages. Estimated sizes are for working Python, tests excluded.

| Module | Replaces | ~Lines |
| --------------- | -------- | ------ |
| `spec_load` | YAML merge, schema sprawl, selector sentinels, context sets | 400 |
| `surface` | `_synthesize_anchor_modifiers`, anchor-subset stances, modifier parsing; cell-binding resolution | 450 |
| `settle` | The semantic content of ~616 calt lookups, `calt_cycle`, the guard/demote/reflip universe, the 172 YAML rows, `_PENDING_BK_ENTRY_GUARDS` | 700 |
| `table` | `_analyze_quikscript_joins`, `_populate_exit_reachability`, topo sorts; emits settlement/treaty tables, joint flags, incomparability errors, `E-STRANDED` invariant | 500 |
| `geometry` | `expand_join_transforms` + context-inheritance plumbing; cell bitmaps, stubs, bound shapes, extensions, anchors, gap math | 450 |
| `defects` | join_analysis warnings, leak classifier/snapshot in-emitter half, audit_anchor_geometry, the one-off ·It·Roe test | 550 |
| `emit_gsub` | `_emit_quikscript_calt` (~4,200 lines) + guards + demotes + reflips + ZWNJ passes | 800 |
| `emit_gpos` | curs/kern emitters | 200 |
| `compile_font` | build_font.py core (largely kept) | ±200 |
| `explain` | inspect_join, shape_sequences, FEA archaeology | 300 |
| `conform` | The sweeps’ correctness burden (example-sequence generation, HarfBuzz diff) | 300 |
| `review` | build_check_html (evolves: treaty-diff rows, verdict exports) | +400 |

Roughly 4.5–5k new lines replacing ~15k of pipeline Python, the seven-script leak constellation, and the 30.8k-line FEA’s role as the only equivalence oracle. The corpus runner (`test_shaping.py`), conftest collection, kerning.html, and the journal/runbook loop carry over with light edits.

## 15. Honest weaknesses and open questions

1. **The settlement-transducer emitter is the load-bearing unbuilt piece.** Mitigated by the week-one prototype with a kill criterion and a designed two-strata fallback (whose own residual within-lookup reliance is noted in §7); not eliminated.
2. **Emergent behavior lives off the page.** The §6.3 compensations (explain tool, provenance comments, cross-referencing derived reports, one-key pinning) are real but are tooling promises; if they ship late, the locality cost arrives early.
3. **Cells could become the new sprawl.** The mint count is bounded by genuine join states (OpenType’s irreducible memory), but the readability claim rests on derived matrices staying small. The per-rune surface report is the watchdog.
4. **Greedy left-to-right settlement will disagree with today’s font in don’t-care spots.** The systematic generator (allowlist polarity) is closed by §13.3’s `toward:`/`from:` migration; the residue is genuinely incidental drift, triaged in the verdict app. A few old emergent outcomes the author silently preferred will still be rediscovered the slow way.
5. **The specificity module fails quietly if it fails.** Hence extensional semantics, stratified evaluation, and its dedicated regression-test class with named cases; this is the one component where extra paranoia is budgeted.
6. **Prefers, bound shapes, and resolves are the soft accretion channels.** Mandatory targets, `why:` requirements, pileup lints, and surfaced per-rune counts (including the migrated-resolve debt number) are visibility guarantees, not impossibility proofs.
7. **Stylistic-set composition is sampled, not proven.** The model defines behavior for all combinations (unlock rows compose by union); conformance verifies each set alone, declared interactions, the Manual’s configurations, and one multi-set combination; the registry documents which combinations are verified. Undeclared combinations ship best-effort, documented — not a build error (the font is user-facing), not silent.
8. **Resolved-context ligature formation is live and built.** The qsLow migration produced the example the original verification had not found (·Day·Utter·Low), and the §5.7 late-formation guard now decides formation per window from the runes’ join surfaces, config-blind and derived rather than authored. The remaining bend points are enforced as hard emitter errors: a guard verdict that differs across capability configurations, or one that blocks at a boundary second slot without blocking everywhere, cannot be expressed in the pre-marker formation staging.
9. **The orientation vocabulary is coarse** (`horizontal|vertical|diagonal`), and its values are author judgments with no ground truth but the eye; wrong values produce confident wrong flags. Bitmap-gradient suggestions can help seed it.
10. **The ductus gate is resequenced from core-idea’s stated want.** Core-idea says finish all ductus before the rewrite; this design gates per rune instead (§3.1), trading the global front-load for mechanical enforcement at the binding granularity. Residual risk: motions with neither bitmap nor prose are caught by neither sequencing, and writing the hardest enumerations under conversion pressure may bias toward bitmap-shaped prose. This is an explicit deviation from a recorded author decision, pending sign-off.
11. **Deleted knowledge can be silently lost in corners no gate reaches** (the archive documents real depth-5-only regressions). The per-transition conformance gate closes most of this class; the depth-4 belt stays for the rest, and some taste will still be rediscovered by eye, later. That is the accepted price of deleting rather than porting.

Open questions deliberately left for implementation: whether the locked-twins ZWNJ optimization ever replaces the chokepoint-plus-coverage-transform, and the namer-dot Chrome itemization issue, which stays in the graveyard untouched. (The third — whether any real case ever needs the `right.then` hop widened — closed in 2026-07: the orphaned-·Tea windows first widened it to depth-3, the mid-word ·Utter orphans in ·Day·Tea·Utter·Tea·X widened it again to depth-4, and §3.4 now carries the depth-4 chain grammar.)
