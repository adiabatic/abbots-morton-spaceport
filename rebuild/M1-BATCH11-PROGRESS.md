# M1 batch 11 — qsOut (and qsOut_qsTea)

Scratch for the ·Out migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Parked

Nothing yet.

## State

The rune, the ligature rune (qsOut_qsTea, the batch's formation-closure obligation), the alphabet, the aliases — including `qsSee.ex-y0.ex-con-1`, the one already-migrated neighbor name that only ever appears beside ·Out — the qsSee neighbor edits, the contact-allow blessings, and the two new ledger landings (qsOut and qsOut_qsTea joining `SS10_UNCOVERED_BY_OLD_FONT`, and the `ss03-out-tea-ligature-kept` class) are in. `rebuild/out/m1/oracle_summary.json` is the unmatched census; every unmatched shape that involves ·Out is either a pre-existing verdict-gated family echoed into the wider window universe (the surface's echo grouping folds those into their standing groups) or one of the fresh questions under "Open questions" below.

## Shape of the letter, as modeled

·Out is Short. Two stances: `normal`, the arch — baseline entry at x=0, no exit — and `swept-up`, where the stroke keeps climbing past the crown to an x-height exit whose `toward:` list is oracle-derived (qsDay, qsDay_qsUtter, qsFee, qsNo, qsRoe, qsEt, qsOy). The entry `from:` list is likewise oracle-derived (qsPea, qsTea, qsTea_qsOy, qsDay, qsSee, qsMay, qsNo, qsIt, qsEt, qsOy, qsUtter); unmigrated old-font enterers (qsBay, qsGay, …) get added at their own migrations, the qsAh pattern. One unconditioned-in-spirit yielding prefer (exit over no-exit, except before qsFee) carries the old font's grouping taste: ·Out ~x~ ·No | ·See rather than ·Out | ·No ~b~ ·See, while before ·Fee the un-excepted qsFee forward prefer wins and ·Out yields (·Out.∅ |?| ·Fee ~x~ ·Jai, but ·Out.ex-ext-1 ~x~ ·Fee | ·Utter where ·Fee's forward join is suppressed).

The after-·See forms are the runes' first live `bind:` contracts: each stance carries a `raked` redraw — the raked leg that fuses into ·See's full `straighter` tail one row above the baseline — swapped in by a bind contract keyed on `left: qsSee`, with `trim: 1` blanking the redraw's never-rendered y0 foot after the bind's convention anchor lands on it. The bind bitmap carries its own geometry, so the anchors ride it implicitly and no See-scoped exit extends exist; qsFee's ordinary +1 composes on top for ·See·Out·Fee. The fusion is a deliberate ink overlap and the seam row is ·See's alone, which is why the blessing block in `rebuild/m1-contact-allow.yaml` covers the overlap and seam-gap spellings.

The ligature is a single `hapax` stance, entryless nowhere but exitless everywhere: the old font compiles no exit-bearing variant, so followers always arrive unjoined, and its own `raked` bind (trimmed like the letter's) handles the after-·See form.

## What the neighbors already knew

qsFee's and qsOy's x-height entry from-lists, qsSee's `straighter` toward-list, forward-baseline prefer and contract, and qsTea's two ss03 unlock left-lists all named qsOut before this batch (all oracle-verified at their own migrations). The batch's neighbor edits are the three qsSee records gaining `qsOut_qsTea` beside `qsOut` — M1 names ligature families explicitly, and the old font's See-side contract demonstrably fires before the ligature too. The receivers' x-height entries (qsDay, qsDay_qsUtter, qsNo, qsRoe, qsEt) are from-unrestricted, so no edits there; ·Out's own toward-list does the gating.

## Recorded design overrides

- **·Out+Tea always forms** (the user's ruling: "do what ·Tea·Oy does"). M1 formation is config-blind by design and the old font's ss03 split (·Out ~x~ ·Tea.half via the `_LIGATURES_ALLOWING_SECOND_COMPONENT_FWD_VARIANTS` carve-out) cannot coexist with the default-config ligature, so the ss03 arm is retired wholesale as the `ss03-out-tea-ligature-kept` ledger class (`intended`, `no_verdict`). The mechanism is qsTea's two exit refusals keyed on an unjoined-·Out left — a context only the section 5.7 guard's trail simulation can produce, since mid-run ·Out·Tea always forms — which defuse the guard that would otherwise un-form the exitless ligature before every reachable follower.
- **The ss03-gated records died with that ruling**: the old `extend_exit_before_gated: {ss03: qsTea}` and a toward-list qsTea entry were drafted and then removed — under always-form no ·Out ~x~ ·Tea window exists for them to serve. qsTea's two ss03 unlock left-lists still name qsOut from the pre-batch wiring; those arms are now unreachable and stand as documentation of the old behavior — retiring them is the sitting's call, not this batch's.
- **The `*_qsUtter` guard classes gained qsOut for free**: the old font un-forms qsDay/qsSee/qsVie+Utter before ·Out (alt-·Utter takes the baseline join), and M1's guard reaches the same verdicts from the runes alone; only the emitted class membership test moved.
- **Oracle-verified-only lists**, as every batch: the from/toward lists transcribe what the subset baselines witness, nothing more.
- **The ·See·Out interlock is grounded, not discarded** (the sitting's one fresh complaint, plus the follow-up ruling): ·See keeps its full `straighter` tail — the by-1 contract is gone — and each ·Out after-·See `raked` redraw is one column shorter with `trim: 1`, so the leg fuses into the tail one row above the baseline and the tail's kick-left pixel grounds the seam. That recreates the old font's ·See·Out exactly: the old `ex-con-1` was a tuck that kept the tail's baseline ink (the first transcription wrongly blanked it — the complaint), so every after-·See form is pixel-identical to the shipped font, absorbed machine-approved by the `see-out-fusion-respelled` ledger class. The fusion is a deliberate ink overlap, so the contact-allow blessing block returns in the new spellings. The user's rationale is the comment on qsSee's `straighter` baseline exit row and the `why:` on qsOut's arch contract.
- **The via-lead enumeration fix, in both engines**: qsOut_qsTea is the first ligature whose trail (qsTea) is another ligature's lead, so a post-formation stream can spell the formation-impossible adjacency "bare ·Out before qsTea_qsOy" — raw ·Out·Tea·Oy, which greedy formation always resolves to qsOut_qsTea — wearing the follower's ligature name, where the window enumeration's formation-pair filters couldn't see it. The conform gate caught it as dead decision-table transitions. `_formation_pairs` and `_survivable_formation_windows` (and their kernel-rs twins in `options.rs`) now carry via-lead keys — `(lead, L)` for every ligature L led by the pair's trail — so every existing filter, the class-grain fiber derivation included, prunes through the shared pipelines unchanged.

## Open questions for the sitting

- **The X·Utter·Out strict gains** (exemplar keys `E658:E67A:E67B`, `E665:E67A:E67B`, `E67A:E67A:E67B`, `E670:E672:E67B`'s ·Utter siblings): wherever alt-·Utter serves ·Out at the baseline, M1 also takes alt-·Utter's x-height entry backward from an x-height-exiting left, where the old font left that seam broken. Two joins against one — the recorded "alt-·Utter only before ·Low or on strict gain" principle suggests approval, but the windows are fresh.
- **The ·No regrouping before ·Out** (`E650:E666:E67B`, `E652:E666:E67B`): old renders half-·Pea/half-·Tea ~x~ ·No | ·Out; M1 regroups to ·Pea/·Tea ~b~ ·No ~b~ ·Out on join-count. The bare pair (without ·Out) is already a standing unmatched family; the ·Out arm adds the seam-gain flavor.
- **·Out|·Tea+Oy**: `standing-approvals.yaml`'s `tea-oy-ligature-break` carries `except_left: [qsOut]` on purpose — those units queue for hand adjudication; don't blanket them.
- **·Day·Tea·Out / ·No·Tea·Out**: the yield lists are closed; these windows queue in `regrouping-floor-drift` with the answer on record — a glance, not a decision.

## The WHATNEXT items this batch was supposed to answer

- **qsFee's forward-preference left scope**: ·Out arrived implicating exactly the predicted nothing on qsFee's side — the seam taste landed as ·Out's own prefer except-qsFee, so qsFee's prefer scope is untouched and no paired carve-out was needed.
- **The dormant qsOut-keyed contract on qsSee's straighter exit**: woke, fired, and the sitting re-adjudicated it away — see the design override above.
- **·See's grounded-exit tie, ·Out arm**: the grounded exit still wins the seam, but on `straightest` by the sitting's ruling; the oracle divergence is deliberate.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal   # rune loads
uv run python -m rebuild.pipeline.run_m1 --jobs 6                   # build + gates + oracle (refreshes the baseline subset itself)
PYTHONPATH=. uv run python rebuild/tools/probe.py E67B:E652         # one window, all configs
make test-rebuild
make test
make artifact-cycle ARGS='--update-pins'
make verdict-ready
```

## Resume

```zsh
make review-cycle ARGS='--update-pins'
```
