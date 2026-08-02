# M1 batch 8 — qsEt

Scratch for the ·Et migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Committed

- `Don't join ·It·Et (it looks kind of ugly)` — the qsIt x-height-exit refusal toward qsEt; the ·It·Et windows that matched the old font re-queue as deliberate divergences at the next cycle.
- `Contract ·Et·May by a pixel` — the qsEt baseline-exit contract toward qsMay (plus the ·Low-riser contact re-blessing under the contracted twin's name); every approved ·Et·May sharer re-queues as deliberate churn at the next cycle.

## Parked

Nothing yet.

## State

The rune, the alphabet entry, the aliases and the smoke rows are in place. `run_m1` builds green: the conform sweep is exact (the ss05 both-baseline unlock and qsTea's row-grain refusals went live with qsEt and conform-verified), the boundary-equivalence sweep is exact, and every in-scope Manual pin replays clean — the ·Et pins became gate-eligible with this batch and none disagreed. The defect gates needed one blessing pass in `rebuild/m1-contact-allow.yaml`: ·Et's declined-exit tail (the E-DANGLE quartet, on the qsRoe/qsAh terminal precedent), the ·It/·Low riser corners one row off the y5 seam, and the ss05 both-baseline ·Tea's y1 corner into ·Utter. The oracle's unmatched rows are up, as a new letter always puts them, and are verdict-gated rather than failing (`oracle_summary.json`). The qsRoe sitting closed by declaration first — all remaining blanks bulk-approved with a `[bulk-close]` note, on the qsI precedent — so the store enters this batch with no open blanks and the frontier carry aligned.

`rebuild/pipeline/run_m1` takes `--jobs`, and the table stage is most of its wall time: `--jobs 6` turns a fifteen-minute build into a three-minute one on this machine. Worth remembering for any iterate-and-rebuild loop.

## Shape of the letter, as modeled

·Et is a Short single stroke: down the left edge from the x-height, then sloping to a rightward tail along the baseline. One entry (x-height, on the vertical), one exit (baseline, off the horizontal tail), every entry/exit combination legal, so the rune has no pairings block and no cells. The tail is base ink — the old font uses the bare drawing as the live join carrier (`qsEt | qsEat` joins with both names bare) — so there is no stub and no withdrawal variant. The one derive is transcription: the x-height entry extends by a pixel after the halves trio (·Pea/·Tea/·He), which is the shipped `halves_exit_xheight` context set written in qsRoe's plain-family idiom.

The ductus is drafted, not transcribed — the shipped YAML carries none for qsEt — and is marked `# DRAFT — pending author sign-off` in the rune.

## What the neighbors already knew

Most of the wiring predated this batch: qsTea carries the ss05 both-baseline unlock scoped `left: qsEt` plus the two row-grain refusals that strand an unentered ·Tea after ·Et; qsFee and qsI both name qsEt in their x-height exit extends; qsFee's exit `toward:` names it; qsPea's baseline entry admits it and the user's recorded refusal blocks it, matching the oracle's break. The two edits this batch made: qsAh's baseline `from:` gains qsEt (oracle-verified — `·Et ~b~ ·Ah` is a real join, and the list is the one with the recon-derived tail), and qsNo's flipped baseline `from:` gains qsEt (`·Et ~b~ ·No.alt` is the default-config rendering).

## Recorded design overrides

- **The entry is unrestricted where qsAh's carries a from-list.** Every migrated x-height exit that reaches ·Et joins it in the old font; the one that doesn't — ·Roe — already excludes qsEt from its own `toward:` list, so the entry needs no allowlist. The qsRoe idiom, chosen over the qsAh one.
- **qsPea and qsTea each carry a half-stance prefer before ·It·Et.** ·Et's arrival gave an unentered ·It its first x-height acceptor, which turned every `X ~x~ ·It ·Et` window into a two-join tie: serve ·It backward (the old font's uniform answer, `y5,break` for every x-height exiter) or let ·It reach forward into ·Et. For most lefts the floor's realize-left-seam picks the backward join, but ·Pea and ·Tea rank `order: [full, half]`, and the stance order outranks the floor — the non-joining full bar beat the half's grounded join and flipped the seam (this was the Manual-pin gate's one disagreement, the `·Tea ~x~ ·It | ·Et ~b~ ·Roe` pin at `site/the-manual.html:3935`). The record is the schema's stance-grain tie-breaker (`stance: half, when: {right: {family: qsIt, then: {family: qsEt}}}`, default yields-to-joins) on both runes; in `·Et ~b~ ·Tea ·It ·Et` the half has no baseline entry, so the prefer self-neutralizes and ·Tea still spends its slot backward. The flip ended mid-sitting: the user's reject on `·Pea·No·It·Et` landed a qsIt refusal of the ·It·Et x-height join, which makes the forward seam unrepresentable — the backward join now falls out of join-count, and qsHe needs no analogous record (WHATNEXT's qsHe bullet records the live state).

## Open questions for the sitting

- **`·Et ~b~ ·Tea` outranks even ·Tea·Day.** The shipped font strands ·Day (`·Et ~b~ ·Tea | ·Day`) in the default config, and qsTea's refusals reproduce that; expect no divergence there. Under ss05 the old font gives ·Tea its both-baseline pairing but still dangles the exit before ·Day specifically (`y0,break`) while ·No/·It/·Roe/·See/·Utter chain on — if M1's ·Day accepts the join the old font never draws, that is a strict-gain divergence to adjudicate.
- **ss05 un-forms ·Tea+Oy after ·Et in the old font** (`·Et ~b~ ·Tea | ·Oy` with the both-baseline ·Tea), but M1 stages set markers after formation precisely so a set cannot un-form a ligature — expect M1 to keep `·Et | ·Tea+Oy` and diverge under ss05.
- **`·Day ·Tea ·Et` joins the ·Day·Tea·X yield family.** The shipped font yields ·Day and joins `·Tea.half ~x~ ·Et`; M1 keeps the ·Day~·Tea join, same as the family's other members. A family question per the standing WHATNEXT fork, not a qsEt one.
- **`·Et ~b~ ·Pea` joins in M1 where the shipped pair breaks.** The shipped YAML declares the intent — qsPea's `entry_baseline_exit_noentry` stance (`entry: [3, 0]`, selected after ·Et/·Awe) exists precisely to receive this join, and the rune's `from: [qsEt, qsAwe]` transcribed it in batch 1 — but the shipped font's own pair behavior never fires it (`E672:E650` breaks in every config). M1 realizes the declared anchor; the oracle logs the seam gain (the `00B7:E672:E650` exemplar). Faithful to the stance, divergent from the behavior — one for the sitting.

## The WHATNEXT items this batch was supposed to answer

- **The ss05 row-grain refusal coverage re-check** — came due at this migration; the unlock and both refusals go live with qsEt in the alphabet. Verified by the conform sweep (see State).
- **The ·Tea·Day tight bond** — qsEt can exit into ·Tea, and needed neither shape: the shipped font itself spends ·Tea's baseline slot backward after ·Et, and qsTea's own records (the refusals plus the scoped ss05 unlock) already carry that exception.
- **qsFee's forward-preference left scope** — nothing needed. ·Et never enters ·Fee (no x-height exit), and on the follower side `·X ·Fee ·Et` rides qsFee's existing exit-extend, which has named qsEt since batch 4.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal   # rune loads
uv run python -m rebuild.pipeline.run_m1 --jobs 6                   # build + gates + oracle (refreshes the baseline subset itself)
PYTHONPATH=. uv run python rebuild/tools/probe.py E672:E652         # one window, all configs
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
