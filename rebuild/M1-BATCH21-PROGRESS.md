# M1 batch 21 — qsThaw

Scratch for the ·Thaw migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Parked

- **Two scope widenings on other runes' user-ruled prefers are agent-transcribed from the oracle, not fresh user rulings — confirm at the sitting.** qsIt's after-·Key yielding prefer gains qsThaw (the old font has ·It give ·Key's entry up to serve ·Thaw: `E654:E670:E656` lands break,y0), and qsUtter's alternate hand-off prefer gains qsThaw (before ·Thaw the old font breaks every left's ·Utter seam — the ·Day+Utter, ·Vie+Utter, ·See+Utter and ·J'ai+Utter formations included — so the alternate ·Utter can serve ·Thaw at the baseline, the `·Day | ·Utter.alt ~b~ ·Low` shape with a new follower). The two records meet on the `X ·It ·Utter ·Thaw` windows, where the kernel refused the non-nested overlap (E-INCOMPARABLE), so ·Thaw also joins the ·Utter carve on all three of qsIt's yielding prefers (the `qsUtter then` except lists): ·Utter's own records rule that seam, M1's floor keeps the backward join the old font isolates (`E654:E670:E67A:E656` lands break,break,y0 in the old font), and the window diverges as a seam gain for the sitting, the standing ·Bay/·Key shape.
- Expected sitting divergences with no record authored, all standing shapes recurring behind the new letter: ·Oy·It·Thaw keeps M1's ·Oy·It baseline gain and breaks to ·Thaw where the old font isolates ·Oy and joins ·It into ·Thaw (the `oy-it-baseline` family's shape); the X·Utter reach-back gains recur with ·Thaw as the follower (`may-utter-gains` and kin, wherever a left in the alternate's x-height from-list can now buy two joins); and the bare-carrier lefts (·Bay, ·Day, ·Et, ·Eight, ·Awe, ·Ox, ·Oy, ·Ooze) land their name-grain rows under `bare-name-live-join` as every migration's do.

## Recorded design overrides

- ·Thaw is one stance and the alphabet's first entry-only rune: a Tall hook-and-stem whose baseline entry admits fifteen migrated lefts on the bare drawing (entry anchor [2, 0], the standard leftmost-ink placement), with no exit rows at all. Its one old-font exit — `exit_baseline` at [3, 0], toward ·-ing alone — waits for qsIng's migration per the migrated-partners-only allowlist idiom; qsIng's own evidence sweep will resurface it.
- The old `noentry_after_tall` stance re-spells per the rubric as nothing at all: same bitmap, entry anchor removed after the `talls` context set. In M1 the break falls out of the entry's `from:` scope — the Tall lefts are simply not members — so no stance, no refuse, and `qsThaw.after-tall` aliases to the bare hapax cell.
- No entry-side adjustments exist to transcribe: the old font enters bare qsThaw everywhere (three compiled forms total — bare, after-tall, noentry), so the rune carries no extend, contract, stub, or bound sibling.
- Pre-wired records going live, none needing an edit: qsRoe's toward-list membership, qsJai's by-1 exit extend (`qsJai.en-y5.ex-y0.ex-ext-1|qsThaw` in the pair rows), qsTea's user-ruled two-verticals refuse, and qsMay's entered-·May refuse (`self: {entry: live}`, right qsThaw/qsYe) — the last holds `X ~?~ ·May | ·Thaw` broken wherever ·May is entered, which the oracle confirms (`E67A:E665:E656`, `E655:E665:E656` both land joined-then-break).
- qsThaw joins `SS10_UNCOVERED_BY_OLD_FONT` on the qsOut precedent, entry side only: the bare cmap glyph carries the live entry anchor, so the old overlay keeps every join into ·Thaw wherever the left's exit anchor also survives — and qsPea/qsKey rejoin under ss10 alone, the after-tall break being itself a calt the overlay disables. The set's comment and the `ss10-isolation-completed` why carry the evidence; no new ledger class and no classifier arm.
- The ·Tea·Day bond got its owed glance: ·Thaw has no exit side, so no ·Thaw·Tea seam exists, no yielding prefer is needed, and no ·Tea slot can be spent backward on ·Thaw's account (·Tea is not in the entry's from-list either — the pre-wired qsTea refuse and the allowlist agree).
- The census pins' invariant block moves once: `zwnj-word-initial-unification` enters the machine-approved class list, because ·Thaw is the first letter to land units under the class's surviving namer-dot remainder — the old pipeline minted `qsThaw.noentry` shadows after the namer dot, the locked twin's drawing is identical, and the rows are ink-identical name-grain divergences the class was kept to document. No ledger edit; the class and its `why:` predate the batch.
- The entered-vs-entryless ties all break the old font's way with no record: a left entered at the baseline that cannot also serve ·Thaw (·Gay's crossover pairing, ·It's pairings, ·Roe's never-pair, alt-·No's sibling) keeps its backward join and breaks forward, matching the oracle (`E653:E655:E656`, `E651:E670:E656`, `E651:E668:E656` all land joined-then-break), while the x-height-entered forms ride through to serve ·Thaw at the baseline (`E67A:E655:E656`, `E652:E670:E656` with ·It's standing by-1 exit extension, `E67A:E668:E656`).

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
PYTHONPATH=. uv run python rebuild/tools/probe.py E651:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E650:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E67A:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E654:E670:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E653:E655:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E67A:E655:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E653:E67A:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E659:E67A:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E652:E670:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E67A:E665:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E651:E666:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E652:E679:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E655:E65D:E656
PYTHONPATH=. uv run python rebuild/tools/probe.py E656:E652:E653
make test-rebuild
make test
make artifact-cycle
make verdict-ready
```

## Resume

```zsh
make review-cycle
```
