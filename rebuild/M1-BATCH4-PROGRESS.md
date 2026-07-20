# M1 batch 4 progress — qsFee

Scratch progress file for the in-flight qsFee migration; delete at batch close, lifting surviving forward-pointers into WHATNEXT.md.

## Committed

- (pending) Add ·Fee

## Parked

- **Ductus DRAFT sign-off** — `qsFee.yaml`'s `loop` motion folds the old `continues_stroke_from_top` prose into the family ductus (the top-join shape is a `stub`, not a motion) and carries the `# DRAFT — pending author sign-off` marker. The `reversed-loop` prose carries byte-for-byte minus "Cannot exit anywhere.", which moved to structure (no exit rows plus `require: [entry]`); that structural move also awaits sign-off.
- **The new contact-allow signature** — `contact:qsIt.hapax.en-y0.ex-y0:qsUtter.mono.en-y0.ex-y5.ex-ext-1:y1` was added bare as a mechanical sibling of the blessed ·It·Utter corners (the baseline-entered twin; baseline-proven by `E666:E670:E67A:E658`). Blessing prose is the user's if wanted.
- **The ss03 ·Fee·Tea extension is a deliberate divergence** — the rune extends by 1 (the old derive's stated intent) where the shipped font reused the before-may ext-3 variant. It lands through verdicts like the ·It·Low fork, not a ledger entry: the affected ss03 windows sit among the batch's unmatched rows and queue as blanks for the sitting.

## Design overrides

- **The M1-PLAN §3 qsMay after-·Fee contract is retired** — the shipped qsMay already binds `pulled-back-stubless` on its x-height entry row for every enterer, so activating ·Fee took only a `from:` widening; the modeled `contract:` record would name a bitmap the row binding already selects and could never demonstrably fire. The fixture copy in `rebuild/pipeline/fixtures.py` stays as the deferred-partner exemplar.
- **qsFee stays out of `SS10_UNCOVERED_BY_OLD_FONT`** — the old ss10 overlay substitutes every qsFee variant to the bare cmap glyph, which carries no cursive anchors, so the old font isolates ·Fee correctly and the 787 ss10 seam-loss rows ride the existing ss10-isolation-completed class.

## Verification recipe

- `uv run pytest rebuild/ -n auto --dist worksteal` — expect exactly the four documented baseline failures plus the artifact-pinned census tests until the cycle re-pins.
- `uv run python -m rebuild.pipeline.run_m1 --jobs 8` — defect/boundary/Manual-pin gates pass (pin gate 32/32; the alphabet growth pulled 3 more pins into scope); budget gate yellow-steady (headroom ~43 KB); exits nonzero at the oracle stage on the verdict-gated rows, expected until the sitting lands verdicts.
- Oracle direct: expect `multi_matched: 0`, `unmatched: 13194` — the 9,490 standing verdict-gated taste rows plus 3,704 new ·Fee windows (join gains over old dangling/unextended exits, ·No/·Utter regroupings behind an unjoined ·Fee, the before-may/ss03 reuse patterns, and the deliberate ss03 by-1 rows).
- `uv run python -m rebuild.pipeline.run_m1 --conform-only --jobs 8` — must be exact (13-symbol alphabet).
- `make test` self-skips (the shipped legacy pipeline is untouched); `uv run pytest rebuild/ ...` is this batch's gate.

## Resume

1. `make review-cycle` (artifact cycle: snapshot, run_m1, surface rebuild, verdict carry + merge, census re-pin, gates), then `make verdict-ready`.
2. Prepare the sitting with the review-docket skill (echo prefill, docket bake), then adjudicate the ·Fee blanks in the app's docket view (`#view=docket`).
3. At batch close: delete this file, lifting surviving forward-pointers into WHATNEXT.md.
