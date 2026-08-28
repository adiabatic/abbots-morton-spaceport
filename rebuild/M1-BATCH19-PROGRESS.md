# M1 batch 19 — qsKey

Scratch for the ·Key migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Parked

- The drafted `ductus` awaits the author's vet; the `# DRAFT` marker is the worklist.
- **The ·Key·Utter·May yield is agent-transcribed from the Manual pin and the old font, not a fresh user ruling — confirm or re-scope at the sitting.** The pin `·Bay ~b~ ·It | ·Key | ·Utter.alt ~b~ ·May` (site/the-manual.html) came into the no-waiver gate's scope with ·Key and demands the alternate-·Utter shape, while M1's floor otherwise keeps `·Key ~b~ ·Utter ~x~ ·May` (the standing shape every other baseline left renders today — the probe shows `·Bay·Utter·May` as y0,y5 too, riding earlier sittings' verdicts). Expressed at the narrowest grain that satisfies the pin without touching those standing windows or qsUtter's user-voiced records: a qsUtter refuse of the mono x-height exit scoped to left ·Key right ·May, which turns the window into a tie, plus a yielding prefer on qsKey (`{exit: none}` over `{exit: baseline}` before ·Utter-then-·May) to break it toward the alternate. The ·Bay-precedent alternative — yielding for every left by widening qsUtter's alt-yield — is the broader ruling only the user can make, and would re-litigate the standing approvals.

## Recorded design overrides

- ·Key is one stance: a Tall angle slanting in from the top left and back out to the bottom left, closing with a rightward foot on the baseline. One top entry at the start of the slant, admitting exactly ·See (the only letter the old font ever joins into it, on both bare glyphs at y8), and one baseline exit at the end of the foot, reaching the same eighteen receivers ·Awe's does — both allowlists transcribed from the old font's pair rows.
- ·Key yields its ·Tea join wherever ·Tea gains a forward join by it — the qsJai yielding prefer, verbatim, on the same oracle shape as ·Awe, ·Ox, ·Eight, ·Ooze, and ·Bay.
- **·It yields ·Key's baseline join to every follower it can serve forward** (user ruling at this batch): the ·Bay ruling's shape, widened to the whole reachable set, because the old font's own scoping is what widens it — ·Key is absent from the `not_after` list that restricts the entryless forward exit, transcribed at ·It's migration as the qsBay/qsDay/qsGay/qsShe refuse, so nothing after ·Key holds the exit back. The list is not curated: ·Key's baseline exit reaches eighteen families, ·It's entryless-exit refuse blocks ·Tea/·Roe/·It, and the remaining fifteen are the record's targets. M1's entered-vs-entryless tie otherwise breaks backward, so it takes a second yielding prefer on qsIt (left qsKey) with two carves, both of them E-INCOMPARABLE guards verified by deletion: the ·Bay record's ·Utter carve verbatim, against qsUtter's own records — and on the ·Utter chains toward ·Vie/·See/·Low the old font has ·It joining nothing at all, a fewer-joins shape M1's floor cannot pick, so those keep the ·Key ~b~ ·It join and diverge as seam gains, exactly as ·Bay's do — and an ·At-then-·May carve against qsAt's falling prefer. The ·No target is inert and stays listed: before ·No the entered cell exits at the x-height, so the `over:` clause never matches and join-count settles the seam two-to-one first; `·Key·It·No` therefore keeps M1's x-height join against the old font's forward baseline one, a divergence only a `mode: absolute` record could reach and one the ruling deliberately leaves alone.
- The pre-wired after-·Key entry extends go live: qsTea's (`left: [qsKey, qsJay]`) and qsIt's (`left: qsKey`), both transcribed at those letters' migrations, now fire in every entered window; nothing new is authored on either side.
- The declined baseline exit is dangle-blessed, not proven: the foot is unconditional letterform ink and its terminal pixel has no ink above it, so `withdrawal: safe`'s vertical-continuation proof cannot hold — ·Awe's, ·Eight's, and ·Bay's shape.
- qsKey joins `SS10_UNCOVERED_BY_OLD_FONT` on the qsAwe shape: no stances in the old record, both anchors riding the base cmap glyph, so bare qsKey keeps its seams under the old overlay on both sides at once (qsSee|qsKey stays y8, qsKey|qsVie stays y0).
- The ·Day/·No ~b~ ·Tea yield lists got their owed glance: ·Tea cannot reach ·Key (·Key's entry admits only ·See, at the top), so no `·Day·Tea·Key` / `·No·Tea·Key` regrouping window exists to adjudicate.
- The ·Key·It·Et window inherits the standing ·It·Et divergence: the old font renders `·Key ~b~ ·It ~x~ ·Et`, M1's user-ruled x-height refuse keeps ·It on ·Key's join instead — the same shape every migrated left already shows.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
PYTHONPATH=. uv run python rebuild/tools/probe.py E65A:E654
PYTHONPATH=. uv run python rebuild/tools/probe.py E650:E65A:E654
PYTHONPATH=. uv run python rebuild/tools/probe.py E654:E652:E653
PYTHONPATH=. uv run python rebuild/tools/probe.py E654:E67A:E652:E653
PYTHONPATH=. uv run python rebuild/tools/probe.py E654:E670:E653
PYTHONPATH=. uv run python rebuild/tools/probe.py E654:E670:E67A:E667
make test-rebuild
make test
make artifact-cycle
make verdict-ready
```

## Resume

```zsh
make review-cycle
```
