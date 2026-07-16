# M1 batch 3 progress — qsLow

Scratch progress file for the in-flight qsLow migration; delete at batch close, lifting surviving forward-pointers into WHATNEXT.md.

## Committed

Nothing committed yet; the whole batch sits in the working tree awaiting review.

## Parked

- **·Day·Utter·Low late-formation fork** — the one Manual-pin disagreement (`·Day | ·Utter.alt ·Low`, site/the-manual.html:1721) and its 133 oracle windows. The old font never forms qsDay_qsUtter before ·Low (·Utter pre-flips to alt to reach ·Low at the baseline); the new model forms ligatures unconditionally. Options: implement §5.7 late formation, or re-transcribe the pin. Recorded as the open fork in WHATNEXT.md; blocks `run_m1`’s pin gate and therefore the artifact cycle.
- **Fresh qsLow verdict rows** — ~2,064 taste-call rows joining the existing batch-2 verdict families (Pea·No regrouping with ·Low appended, alt-·Utter join gains, the ratified ·It exit-extension carve-out before ·Day, ss03 chain gains). They await the next review sitting like the 6,564 batch-2 leftovers; none is a qsLow authoring defect.
- **Ductus sign-off** — qsLow’s drafted `hapax` way carries `# DRAFT — pending author sign-off` (the letter had no ductus prose in `glyph_data/quikscript.yaml`).

## Design overrides

None. The rune transcribes today’s YAML faithfully (four derive rules, two anchors); its qsSee/qsFee/qsJai records load as deferred-partner.

## Verification recipe

- `uv run pytest rebuild/test_spec_load.py rebuild/test_surface.py -n auto --dist worksteal` — expect exactly the four documented baseline failures.
- `uv run python -m rebuild.pipeline.baseline_subset` — regenerate the 12-symbol subset tables (needed once after the alphabet widening; 22,620 rows per config).
- `uv run python -m rebuild.pipeline.run_m1 --jobs 8` — defect/boundary gates pass; stops at the Manual-pin gate on the parked fork above.
- Oracle direct (bypasses the pin-gate stop): `uv run python -c "from rebuild.pipeline import run_m1; run_m1.run_oracle(spec=run_m1.load_default_spec(), jobs=8)"` — expect `multi_matched: 0`, `unmatched: 8761`.
- `uv run python -m rebuild.pipeline.run_m1 --conform-only --jobs 8` — passes (271,452 sequences, 0 divergences).
- `make test` and `uv run pytest rebuild/ -n auto --dist worksteal`; then `make all` + `shasum -a 256 site/AbbotsMortonSpaceportSansSenior-Regular.otf` == `3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35`.

## Resume

1. Decide the late-formation fork (WHATNEXT.md “Open fork”).
2. Re-run `run_m1 --jobs 8` to green, then the artifact cycle: `uv run python rebuild/tools/artifact_cycle.py --verdicts rebuild/evidence/verdicts-carried-7d6cc45.json --update-pins --jobs 8`.
3. Review sitting over the fresh qsLow units (import the carried master, then `make review-serve`).
4. Sign off qsLow’s ductus (grep `# DRAFT`).
