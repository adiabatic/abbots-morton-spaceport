# M1 batch 16 — qsAt

Scratch for the ·At migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Parked

- The drafted rising and falling `ductus` descriptions await the author's vet; the `# DRAFT` markers are the worklist.

## Recorded design overrides

- ·At has a normal rising stance with a baseline entry and x-height exit, plus an entryless falling stance before ·May with a baseline exit. The falling stance re-spells the old five-column exit extension as a three-column extension from a later anchor, preserving the ink without the old overlap.
- The old before-·J'ai exit tuck is re-spelled as ·J'ai's existing receiver-side entry contraction; ·At suppresses its after-·See entry extension in the same windows.
- ·See's grounded-exit preference stands down only before ·At·May, allowing ·At's falling stance to win without changing the surrounding ·See preferences.
- The ·See→·At placement delta is the grounded ·See's own left column, not anything about the seam: the old font's compiled `ex-y0` ·See carried two blank columns before its ink and the rebuild's `straighter` shape carries one, so the letter is a column narrower and everything after it slides. Ruled approve for good; `rebuild/standing-approvals.yaml`'s `see-grounded-left-column-dropped` is the binding record and fills the windows.
- The Manual's ·At·No·Utter·May chain is expressed by two narrow refusals: ·No stops before ·Utter only when it was entered from ·At, and ·Utter takes its baseline arm before ·May only after that stopped ·No seam. Word-initial ·No·Utter·May keeps its approved joins.
- ·At joins `SS10_UNCOVERED_BY_OLD_FONT` on direct pair evidence: the old overlay leaves the bare letter's entry and exit live, while its contextual before-·May and before-·J'ai forms isolate correctly.
- The x-height cap contacts its receivers' left stroke at y4 exactly as the old font does. `rebuild/m1-contact-allow.yaml` carries the exact signatures rather than a broad allowance.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1 --jobs 6
PYTHONPATH=. uv run python rebuild/tools/probe.py E674:E665
PYTHONPATH=. uv run python rebuild/tools/probe.py E674:E65D
PYTHONPATH=. uv run python rebuild/tools/probe.py E65A:E674:E65D
PYTHONPATH=. uv run python rebuild/tools/probe.py E674:E666:E67A:E665
make test-rebuild
make test
make artifact-cycle
make verdict-ready
```

## Resume

```zsh
make review-cycle
```
