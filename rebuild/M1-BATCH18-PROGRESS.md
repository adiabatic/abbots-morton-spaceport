# M1 batch 18 — qsBay

Scratch for the ·Bay migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Parked

- The drafted `ductus` awaits the author's vet; the `# DRAFT` marker is the worklist.

## Recorded design overrides

- ·Bay is one stance: a Deep vertical falling from the x-height into a counterclockwise loop below the baseline, with one x-height entry at the top of the vertical and one baseline exit at the end of the loop's closing bar. Both allowlists are transcribed from the old font's pair rows: the entry admits exactly ·I, ·Ah and ·Utter (·Ah and ·Utter reach with their own by-1 exit extends, pre-wired at their migrations; the four `*_qsUtter` ligatures ride in through left-facing trail transparency), and the exit reaches the same eighteen receivers ·Awe's does.
- The old record's `extend_entry_after` toward the x-height-exiting halves is not transcribed: no half joins into ·Bay in any baseline pair row in any config (the compiled-form inventory carries no `en-ext` variant anywhere), so the record could never fire — the qsAwe precedent (which omitted qsPea from its entry extend on the same no-evidence grounds), taken to all three targets.
- ·Bay yields its ·Tea join wherever ·Tea gains a forward join by it — the qsJai yielding prefer, verbatim, on the same oracle shape as ·Awe, ·Ox, ·Eight and ·Ooze.
- The declined baseline exit is dangle-blessed, not proven: the loop's closing bar is unconditional letterform ink (bare qsBay draws it at every word edge), and its terminal pixel has no ink above it, so `withdrawal: safe`'s vertical-continuation proof cannot hold — ·Awe's and ·Eight's shape.
- The `*_qsUtter` ligature runes each gain qsBay on their by-1 x-height exit extend (the qsUtter-derived trailing-component derive, transcribed now per the WHATNEXT rule that each name lands at its family's migration); qsGay and qsThey stay untranscribed there.
- **·It yields ·Bay's baseline join to serve ·Day, ·Day+Utter and ·Utter** (user ruling at this batch): qsIt's pre-wired refuse already permitted the entryless exit after ·Bay, but the entered-vs-entryless tie broke toward the backward join, so the windows landed in `regrouping-floor-drift`; the user chose matching the old font now over leaving them for the sitting, expressed as a yielding prefer on qsIt (entryless baseline exit over the entered cell, left qsBay, right the refuse's own except list) with `except: {family: qsUtter, then: [qsVie, qsVie_qsUtter, qsSee, qsMay, qsLow]}` — on those chains ·Utter's own records rule the seam (the old font renders `·Bay·It·Utter·Low` with ·It joining nothing at all, a fewer-joins shape M1's floor cannot pick, so those windows keep the ·Bay~·It join and diverge as seam gains for the sitting), and the carve dissolves the E-INCOMPARABLEs against qsUtter's widened alt-yield record and its bare-before-·May record. The old font also has alt-·Utter serve ·At/·I/·Ah/·Out/·Ooze on these chains; no qsUtter record exists for those, so no conflict arises there and join count already picks the ·Bay~·It-plus-alt outcome. ·Tea/·Pea lefts keep M1's standing two-join rendering — that divergence predates this batch. ·Bay·At·May lands old-font on qsAt's stance-grain falling prefer with no new record.
- qsBay joins `SS10_UNCOVERED_BY_OLD_FONT` on direct pair evidence, the qsAt shape: the bare glyph's baseline exit survives the old overlay while the contextual en-y5 entry form is substituted away correctly.
- The ·Day/·No ~b~ ·Tea yield lists got their owed glance: ·Tea cannot reach ·Bay (·Bay's entry admits only ·I/·Ah/·Utter), so no `·Day·Tea·Bay` / `·No·Tea·Bay` regrouping window exists to adjudicate.
- **·Utter yields its backward baseline join to the alternate stance before ·Vie and ·See, for every left** (user ruling at this batch): the Manual pin `·Utter ~x~ ·Bay | ·Utter.alt ~b~ ·Vie` came into the no-waiver gate's scope with ·Bay and demanded it, the old font yields identically for ·Pea/·Tea/·Ooze lefts and before ·See, and round 4 had deliberately left the ·Utter-side left-hop family unruled. Expressed by widening qsUtter's existing before-·Low prefer to right `[qsVie, qsVie_qsUtter, qsSee, qsLow]` with `except: {family: qsSee, then: [qsLow, qsAt, qsOut, qsOut_qsTea, qsOoze]}` — when ·See is followed by one of its grounded receivers the old font keeps ·Utter's backward join and lets ·See ground forward (`·Tea·Utter·See·At` renders y0,break,y0), so the carve keeps qsSee's grounded prefer ruling there and dissolves the E-INCOMPARABLE the flat scope raised. ·See+Utter is not named (the ligature takes no entry, so the backward join rightly holds there, as the old font draws it). The record's `why:` still reads as the ·Low-era note — it is the user's voice and awaits the user's own rewording if any.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1 --jobs 6
PYTHONPATH=. uv run python rebuild/tools/probe.py E675:E651
PYTHONPATH=. uv run python rebuild/tools/probe.py E651:E652:E653
PYTHONPATH=. uv run python rebuild/tools/probe.py E653:E67A:E651
PYTHONPATH=. uv run python rebuild/tools/probe.py E651:E670:E653
PYTHONPATH=. uv run python rebuild/tools/probe.py E651:E67A:E652:E653
make test-rebuild
make test
make artifact-cycle
make verdict-ready
```

## Resume

```zsh
make review-cycle
```
