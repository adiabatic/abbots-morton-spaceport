# M1 batch 3 progress — qsLow

Scratch progress file for the in-flight qsLow migration; delete at batch close, lifting surviving forward-pointers into WHATNEXT.md.

## Committed

- 878709b Add ·Low
- 2f18041 Don’t prefer ·Day+Utter so hard that you don’t get ·Day·Utter.alt·Low like The Manual uses
- 11d085c Don’t prefer ·Utter.alt when it only shifts the one join to the other neighbor
- 2217445 Withhold ·Day’s exit before ·Tea·Low so ·Tea joins forward into ·Low
- 8be6139 Give ·Low·Tea and ·Day·Utter·Tea the full-height bar under ss03 even word-finally
- 0835753 Under ss03, prefer ·Utter ~x~ ·Tea.!half to ·Utter ~x~ ·Tea.half
- a4876ea Carry the ·Utter·Tea full-bar ruling onto the surface and re-pin the census

The extension-fork settle (qsLow added to qsIt’s baseline exit-extension `except` list) and its artifact-cycle fallout — the re-pinned census, the carried-master swap to `rebuild/evidence/verdicts-carried-a4876ea.json`, and the regenerated echo-fill/flip import files — land alongside this note in the bookkeeping commit.

## Parked

- **·Low·Oy·Tea·Oy standing approval** — the carried verdict note says auto-approve “no matter what happens around ·Low·Oy”; carrying only preserves verdicts across unchanged renders, so at batch close this needs a durable home (census pin or similar).
- **Sitting forks and residue** — the ·It exit-extension-before-·Low fork is settled (removed before ·Low; the 107 verdicted windows re-queued as blanks). The ·Utter residue is reduced to the ·Day/·Oy·Tea·Utter·Low orphaning plus two note-principles (the x-height-entering ·Utter.alt wish, the ·No.alt-over-·Utter.alt tie rule), all tracked in WHATNEXT under “Doable anytime”. (The word-final full-·Tea-after-·Utter fork is decided: the u-9101 ruling chose the full bar, qsUtter is ungated in full-·Tea’s ss03 x-height unlock, and the flip file executes the ruling on the previously-verdicted windows.)

## Design overrides

- **§5.7 late formation is built** (was: designed but unbuilt). Formation yields per window iff the unformed trailing component could realize a seam toward the follower and the formed ligature could realize none, under every capability configuration — derived from join surfaces, no per-ligature data. Emitted as the `m1_formation_guarded` chaining-context lookup with generated `ignore sub` rows (a sanctioned exemption alongside the namer-dot guard). Design doc §5.7, the §7 formation row, and honest-criticism item 8 are updated in place.
- **qsUtter’s §5.9 follower prefer is scoped to before ·Low** — the sitting’s verdicts rejected the unconditioned form wherever alt-·Utter merely relocated the window’s one realizable join, so the record now reads `{cell: {exit: baseline}, over: {entry: baseline}, when: {right: {family: qsLow}}}`: ties flip to alt only before ·Low (The Manual’s ·Day·Utter.alt·Low), and every strict join gain still engages alt through the join-count rank with no record needed. The earlier structural rejection (“any axis constraint raises E-AMBIGUOUS”) applied to the axis-crossing scopes tried during the fork resolution; the right-family scope is disjoint from `prefer[0]`/`prefer[1]` on the right axis and builds clean.

## Verification recipe

- `uv run pytest rebuild/test_spec_load.py rebuild/test_surface.py -n auto --dist worksteal` — expect exactly the four documented baseline failures.
- `uv run python -m rebuild.pipeline.run_m1 --jobs 8` — defect/boundary/Manual-pin gates pass (pin gate 29/29); exits nonzero at the oracle stage on the verdict-gated rows, expected until the sitting lands verdicts.
- Oracle direct: `uv run python -c "from rebuild.pipeline import run_m1; run_m1.run_oracle(spec=run_m1.load_default_spec(), jobs=8)"` — expect `multi_matched: 0`, `unmatched: 9381`.
- `uv run python -m rebuild.pipeline.run_m1 --conform-only --jobs 8` — passes (271,452 sequences, 0 divergences, 0 uncovered rules/transitions).
- `make test` and `uv run pytest rebuild/ -n auto --dist worksteal`; the shipped legacy pipeline is untouched by the rebuild work.

## Resume

1. Finish the sitting: `make review-serve` (already running if port 7294 answers), import `rebuild/evidence/verdicts-carried-a4876ea.json`, then `verdicts-echo-fill.json` (194 fills), then `verdicts-flip-utter-tea-full-bar.json` (13 approves), then work the 798-blank queue in the app’s docket view (`#view=docket`).
2. Settle the 14 disagreeing echo groups (WHATNEXT, “Doable anytime”).
3. At batch close: give the ·Low·Oy·Tea·Oy standing approval a durable home, then delete this file.
