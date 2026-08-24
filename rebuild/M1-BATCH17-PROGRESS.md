# M1 batch 17 — qsOoze

Scratch for the ·Ooze migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Parked

- The drafted `ductus` awaits the author's vet; the `# DRAFT` marker is the worklist.

## Recorded design overrides

- ·Ooze is one stance with one baseline entry at the foot of its left leg and one baseline exit at the foot of its right leg, no other anchors, no extension or contraction anywhere, and no ligature. Both allowlists are transcribed from the old font's pair rows: the entry admits every migrated baseline exiter the old font joined into bare qsOoze, and the exit reaches every migrated baseline enterer the old font joined out of it; ·May, ·Roe and ·Vie stay unjoined on the entry side because the old font never drew those seams.
- ·Ooze yields its ·Tea join wherever ·Tea gains a forward join by it — the qsJai yielding prefer, verbatim, on the same oracle shape as ·Awe, ·Ox and ·Eight.
- ·See's `straightest` arm reaches its first live receiver here. It draws one blank column fewer on its left than the old `ex-y0-right` form, the same slide the `straighter` arm already has on record, so `see-grounded-left-column-dropped` in `rebuild/standing-approvals.yaml` names `qsSee.straightest` as an after pivot rather than the windows queueing.
- ·X·Utter·Ooze does not ligate, exactly as ·X·Utter·At does not: ·Utter's alternate stance serves the baseline entry instead, and the `*_qsUtter` ligatures carry no baseline exit to compete with it.
- ·Ooze joins `SS10_UNCOVERED_BY_OLD_FONT` on the qsAwe precedent — no stances in the old record, both anchors on the base cmap glyph, so the old overlay keeps its seams under ss10.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1 --jobs 6
PYTHONPATH=. uv run python rebuild/tools/probe.py E65A:E67E
PYTHONPATH=. uv run python rebuild/tools/probe.py E67E:E652:E653
PYTHONPATH=. uv run python rebuild/tools/probe.py E653:E67A:E67E
PYTHONPATH=. uv run python rebuild/tools/probe.py E67E:E67E
make test-rebuild
make test
make artifact-cycle
make verdict-ready
```

## Resume

```zsh
make review-cycle
```
