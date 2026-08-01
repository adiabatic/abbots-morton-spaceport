# M1 batch 7 — qsRoe

Scratch for the ·Roe migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Committed

- 56f0e27 — ·I keeps its regular loop after ·Roe (the sitting's first reject; ·Pea/·Tea/·May/·It still get the smaller loop)

## Parked

`rebuild/test_novelty_order.py`'s four `main` tests were failing on a clean tree before this batch started: 27a0c14 added a `(copied to clipboard)` line after the worklist URL and the tests read `splitlines()[-1]`. They now read the `http://localhost:` line instead. Unrelated to ·Roe, but they were four of the rebuild gate's five unexplained failures, so the batch could not be verified around them. Left unaddressed: running those tests still writes to the real clipboard, because `novelty_order.main` shells out to `pbcopy` unconditionally on darwin.

## State

The rune, the alphabet entry, the aliases and the smoke rows are all in place. `run_m1` builds: the defect gates are clean with no unblessed error and no flag, the boundary-equivalence sweep is exact, and the no-waiver Manual-pin gate replays clean — `rebuild/out/m1/pipeline_summary.json`, `boundary_equivalence_summary.json` and `manual_pins_summary.json` are the records. The oracle's unmatched rows are up on the last cycle, as a new letter always puts them, and are verdict-gated rather than failing (`oracle_summary.json`).

The census pins have **not** been re-baselined: that needs a built surface, so it belongs to the artifact cycle (`make artifact-cycle ARGS='--update-pins'`), which is also where this batch's carry and merge happen. Until then the census-pinned review tests read stale, which the rebuild gate's classifier forgives as `census-hint`. `make verdict-ready` is the standing check once the cycle has run.

`rebuild/pipeline/run_m1` takes `--jobs`, and the table stage is most of its wall time: `--jobs 6` turns a fifteen-minute build into a three-minute one on this machine. Worth remembering for any iterate-and-rebuild loop.

## Shape of the letter, as modeled

·Roe is one stroke between two tips on the left, so its entry and exit are always at opposite heights — the rune says so with a `pairings: never` on both same-height pairs, and the schema's `cells:` rows bind the redrawn `reaches-over-the-bowl` form for the x-height exit while a two-column exit stub carries the baseline one. It brings the first live `contract:` record in the migrated alphabet (the x-height entry pull-in after a half's x-height exit); every other rune's contract is qsJai- or qsZoo-keyed and dormant.

## Recorded design overrides

Each is a deliberate departure from the old font, to be adjudicated in the sitting rather than silently transcribed.

- **·Roe's entry extensions are pure arithmetic, where the shipped font hand-drew them.** `qsRoe.en-ext-1-at-0` and `-at-5` lengthen the entered arm _and_ shorten the other one by a pixel; the rune's `extend` records only lengthen. Width, advance and both anchors agree, so the oracle — which compares cells, seams and positions — cannot see the difference at all, and no review unit will carry it. The delta is one pixel at the far tip of the un-entered arm. There is no mechanism for it: `bind:` is a `contract:` option, and an extension cannot name a hand-drawn form.
- **The x-height entry contraction fires after every half's x-height exit, not only the dipping ·Pea.** The shipped `contract_entry_after` names `{family: qsPea, traits: [half], modifiers: [ex-dips]}`, so `·X ~x~ ·Pea ~x~ ·Roe` skips a pull-in that bare `·Pea ~x~ ·Roe` gets — even though ·Pea's exit geometry is identical either way. A rune's left condition cannot see the predecessor's own entry state (`joined_at` is the seam being decided, not the neighbor's other side), and the shipped split reads as a selector artifact, so the contraction is uniform over ·Pea/·Tea/·He.
- **·Roe would rather be joined into at the x-height than reach on at the x-height.** An unconditioned follower-preference record (design section 5.9, the shape ·Fee carries pointing the other way). It restores `·Pea ~x~ ·Roe | ·Oy` and `·Tea ~x~ ·Roe | ·Oy`, and — being yields-to-joins — stands down wherever the forward path buys the second join, which is what `·No ~b~ ·Roe ~x~ ·Oy` and its ·Roe/·Utter siblings do. This record wants the user's `why:`.
- **·Tea's unentered full bar refuses ·Roe** (a new record on `qsTea.yaml`). The shipped font resolves ·Tea·Roe to the half's x-height exit through stance ordering rather than any rule, and without a refusal the M1 floor takes the full bar down to the baseline instead; the off-anchor contact gate independently rejects that corner. Scoped `self: {entry: none}` so that ss03's `·Fee ~x~ ·Tea ~b~ ·Roe` — which the shipped font does draw — survives; its corner is blessed in `rebuild/m1-contact-allow.yaml`. This record wants the user's `why:`.
- **·I's smaller-loop preference now requires the left neighbor to have joined at the baseline.** A pre-existing record that only misfired once ·Roe was live: `·X ~b~ ·Roe | ·I` gave ·I the tighter loop with nothing joined to it, where the shipped font gives the plain loop. The shipped selector was `{family: qsRoe, modifiers: [ex-y0]}` — scoped to ·Roe actually exiting low — so this is a transcription fix rather than a new taste.
- **qsNo.flipped's baseline entry admits ·Roe**, which is what lets `·X ~x~ ·Roe ~b~ ·No.alt` join as the shipped font draws it.
- **·Utter's alternate refuses its baseline exit before ·Roe** (a new member of an existing `qsUtter.yaml` refuse list). This is transcription rather than taste: the shipped `alt_reaches_way_back` stance carries `not_before: [qsThey, qsRoe]`, and no window in the whole shipped table puts any alt-·Utter into ·Roe. Because `alternate` requires its exit, refusing it there drops the whole stance and ·Utter falls back to the mono form, restoring `·X | ·Utter ~x~ ·Roe`. It is also what makes the Manual pin at `site/the-manual.html:1256` (`·May | ·Utter ~x~ ·Roe | ·Day+Utter ~x~ ·Roe`) replay clean — that pin became gate-eligible with this batch, and without the refusal it was the no-waiver gate's one disagreement. ·They's half of the shipped carve-out is left for the qsThey migration to verify.
- **·Roe's dangle signatures blessed** in `rebuild/m1-contact-allow.yaml`, on the standing qsNo/qsDay/qsAh precedent: both of ·Roe's arm tips end horizontally, three columns short of the exit anchor on their own row, so `verify_withdrawal_safe`'s vertical-continuation test reads each declined side as connector ink. No withdrawn ·Roe exists in the shipped font.

## Open questions for the sitting

Beyond the overrides above, the ·Roe windows that move off the shipped font fall into families, and each family deserves one decision rather than one per window. `rebuild/out/m1/divergence-audit.tsv` and the docket are the enumeration; these are the shapes to look for.

- **`·Roe ~b~ ·May` when a second ·May follows.** The shipped font breaks that seam and joins `·May ~b~ ·May` instead, because its grounded ·May stance carries no entry anchor for ·Roe's forward selector to match; M1 keeps the ·Roe join and breaks the ·May·May seam — which is exactly what the shipped font itself does after ·Day. M1 is the uniform reading; the shipped one is the selector artifact.
- **`·Roe ~x~ ·Oy` gaining the second join.** After ·No, ·Roe or ·Utter, M1 joins ·Roe on both sides where the shipped font joins only backward. A strict gain, and the reason the follower-preference record above is `yields-to-joins` rather than absolute.
- **The "orally" shape, `·Roe | ·Utter.alt ~b~ ·Low`,** goes live with this batch (`site/extra-senior-words.html:186`) and M1 renders it exactly as the shipped font does — ·Roe reaches neither of ·Utter's entries, so the seam breaks and alt-·Utter carries on to ·Low. The medial-·No sibling at line 182 ("finally") is the one still open; nothing about ·Roe changed it.
- **`·X ·It ·Roe` under ss04, where the shipped font is context-sensitive and M1's unlock is not.** ·It's ss04 baseline pass-through is deliberately context-free in the rune, while the shipped font grants it only after ·Day; both readings score two joins before ·Roe, so whichever way M1 settles it, about thirty windows disagree with the shipped font — currently the ones after ·Day, because ·Roe's backward preference takes ·It's x-height exit. Narrowing that preference to fire only before ·Oy trades those windows for an almost identical set on the other side, which is why it was left general.
- **Windows where ·Roe merely rides an already-landed decision.** `·Pea ~b~ ·No.alt` puts ·Roe at the baseline where the shipped font had it at the x-height; qsFee's forward preference beats an ·I or ·Utter backward join before ·Roe exactly as it does before ·No and ·Day; `·Day ·Tea ·Roe` keeps the ·Day~·Tea join rather than yielding, in the same family as the already-diverging `·Day ·Tea ·I` and `·Day ·Tea ·Ah`; and under ss03 the widened full-·Tea x-height entry carries the bar down into ·Roe exactly as it already does into ·Day, ·May, ·No, ·Low, ·I, ·Ah and ·Utter. None of these is a ·Roe question, and none was re-litigated here.

## The WHATNEXT items this batch was supposed to answer

- **qsRoe in qsAh's entry from-list** — re-verified against the oracle rather than the FEA sweep: `·Roe ~b~ ·Ah` is a real baseline join in the shipped font. The entry stays.
- **The ·Day·Tea-before-·No follower set** — ·No.flipped does engage before ·Roe, so the re-adjudication came due. The finding is that `·Day ·Tea ·No ·Roe` behaves exactly like its six already-diverging migrated siblings (·Pea, ·Day, ·Fee, ·No, ·It, ·Oy), where M1 keeps the ·Day~·Tea join and the shipped font yields. Adding qsRoe to qsDay's depth-3 withdraw list would single it out from that family, so the list was left alone and the family is a sitting question, not a qsRoe one.
- **The ·Tea·Day tight bond** — neither shape is needed. ·Roe cannot exit into ·Tea at all (no ·Tea entry is in either `toward:` list), so it can never spend ·Tea's baseline slot; and qsUtter's x-height entry `from:` list does not admit ·Roe, so the `mode: absolute` ·Utter twin is not needed either.
- **qsFee's forward-preference left scope** — no widening needed. The `·X ·Fee ·Roe` divergences are identical to the `·X ·Fee ·No` and `·X ·Fee ·Day` ones on the same left neighbors, so ·Roe is riding qsFee's existing preference, not provoking a new one.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal   # rune loads
uv run python -m rebuild.pipeline.baseline_subset                   # after any M1_ALPHABET edit
uv run python -m rebuild.pipeline.run_m1 --jobs 6                   # build + gates + oracle
PYTHONPATH=. uv run python rebuild/tools/probe.py E652:E668         # one window, all configs
make test-rebuild
make test
make artifact-cycle ARGS='--update-pins'
make verdict-ready
```

Oracle evidence is derived by scanning `rebuild/out/baseline-<config>.tsv.gz` directly; the length-2 block sits right after the length-1 block in canonical order, so the whole pair-level join map for a letter reads without a full pass. Two traps: `seams` is indexed per codepoint boundary, so map glyphs to codepoints through `clusters` rather than by position, and a ligature glyph owns several codepoint indices.

## Resume

```zsh
make review-cycle
```
