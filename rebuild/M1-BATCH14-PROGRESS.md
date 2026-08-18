# M1 batch 14 — qsOx

Scratch for the ·Ox migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Parked

- **The post-migration scaling sweep is owed and rides the RAM tripwire.** Batch 13 measured the Python-arm top rung at about half this box's RAM, and this batch grows the alphabet again; run `bench-the-rebuild/scaling/scaling.py` detached, read the exponents off the whole-ladder fit, and hand any tripwire finding to the speed-up tracker rather than this batch.

## Shape of the letter, as modeled

·Ox is Short — a brief x-height bar, a straight descending diagonal, and a short vertical tail at the baseline. One `hapax` stance sharing the mono drawing (`shape: mono` in the old record): x-height entry at [0, 5], baseline exit at [5, 0], both allowlisted from oracle evidence. Its entry and exit worlds are byte-identical to ·Awe's — the same sixteen sources (the four `*_qsUtter` ligatures included) and the same sixteen receivers — and like ·Awe it forms no ligature anywhere in the old font. Policy is the same two records: the entry extend by 1 after the x-height-exiting halves (qsTea live, qsHe deferred; ·Pea excluded because its half dips instead), and the ·Tea yielding prefer in exactly qsJai's shape.

## What the neighbors already knew

qsRoe's x-height toward-list and qsPea's baseline-exit refuse both named qsOx from their own batches and verified correct against the pair map, so neither file changed; unlike the qsAwe case, no pre-wired arm turned out wrong. The eight added arms mirror ·Awe's exactly: qsAh/qsNo(alt)/qsOut(both stances)/qsOut_qsTea baseline-entry from-lists, and qsFee/qsOut/qsJai_qsUtter x-height-exit toward-lists. Everything else rides existing scopes with no edit, and qsUtter's yielding prefer again gains no arm.

## Recorded design overrides

- **Oracle-verified-only lists**, as every batch; the one policy-record deferred name is qsHe on the entry extend.
- **qsOx joins `SS10_UNCOVERED_BY_OLD_FONT`** on the qsAh/qsAwe precedent, both sides at once — no stances in the old record, so both anchors ride the base cmap glyph.
- **The declined baseline exit is proven, not blessed — and the proof's premise costs contact corners instead.** ·Ox's tail terminal continues vertically (the qsIt idiom), so the exit carries `withdrawal: safe` and none of ·Awe's dangle blessings; but the same vertical drop sits one column left of every baseline follower's downstroke, so `rebuild/m1-contact-allow.yaml` gains the ·Ox tail-against-bar corners in the ·Oy idiom — the full cross product of ·Ox's exiting cells against the baseline-entering follower cells, every one on a baseline-proven join.
- **The one old-font wart needed no new machinery**: the single window where half-·Tea joins bare ·Ox without the entry extension (·Tea·Ox·Tea·Oy — the same shape as ·Awe's) is absorbed by the standing `halves-entry-extension-restored` class.
- **No ligature obligation, no new ledger class, no alias beyond qsOx's own three names** (`qsOx`, `.en-ext-1`, `.noentry` — the qsEt pattern).

## Open questions for the sitting

- The fresh ·Ox windows the oracle census now carries — standing-family echoes dominate; the echo prefill and standing approvals fold most, and whatever queues is the sitting's docket.
- ·Day·Tea·Ox / ·No·Tea·Ox land in `regrouping-floor-drift` per the closed yield lists, with the pre-adjudicated answer on record; reopen only if a seam reads genuinely worse on the surface than the standing shapes do.
- The drafted `ductus` on qsOx awaits the author's vet — the `# DRAFT` marker is the worklist.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal   # rune loads
uv run python -m rebuild.pipeline.run_m1 --jobs 6                   # build + gates + oracle (refreshes the baseline subset itself)
PYTHONPATH=. uv run python rebuild/tools/probe.py E652:E678         # the entry extension, all configs
PYTHONPATH=. uv run python rebuild/tools/probe.py E678:E652:E653    # the yielding prefer, all configs
make test-rebuild
make test
make artifact-cycle ARGS='--update-pins'
make verdict-ready
```

## Resume

```zsh
make review-cycle ARGS='--update-pins'
```
