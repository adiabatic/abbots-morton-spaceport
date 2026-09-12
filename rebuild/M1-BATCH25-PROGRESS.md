# M1 batch 25 — qsCheer

Scratch for the ·Cheer migration. Delete when the sitting closes the batch, lifting any surviving forward pointer into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsCheer.yaml` awaits the author's vet; its `# DRAFT` marker is the worklist.
- The new review units await their sitting. `make verdict-ready` is the readiness authority.
- The default `·Cheer ~b~ ·It ~x~ ·Zoo` and `·Cheer ~b~ ·It ~x~ ·No` keep both joins; under ss04 both seams use the baseline. These cells outrank the forward-only yielding preference and await the sitting.
- Under ss04, ·Cheer·It·Utter followed by ·At, ·I, ·Ah, ·Out, or ·Ooze can keep ·Utter's incoming baseline join and decline its outgoing join. The current ss04 capability and ·Utter preferences govern these windows; their visual trade awaits the sitting.

## Recorded design overrides

The author chooses the two-pixel reach for every ·Gay·Cheer join, including when ·Gay has a joined entry.

The rune carries the old mono and proportional drawings, the baseline-proven x-height entry and baseline exit, and the old entry extension after x-height-exiting halves other than ·Pea. The neighbor scopes include the ligature seams exposed by the longer baseline windows.

The yielding preference before ·Tea follows the existing qsJai record. The smoke block includes the ·Tea·Day chain and its ·Utter continuation.

·It's yielding preference after ·Cheer chooses the forward baseline join when it competes with the backward join. Cells supporting both joins remain eligible. The follower exceptions keep the backward join available when ·At serves ·May or alternate ·Utter serves its own follower. The ss04 both-baseline pairing remains available and participates in the parked ·Utter trade, except before ·Thaw, where it is withheld after ·Cheer; the `why:` on that grant in `glyph_data/runes/qsIt.yaml` carries the reason.

The absolute ·It preference before ·No·Cheer preserves the Manual's `·It ~b~ ·No.alt | ·Cheer` example when ·It has no incoming join. Its `why:` is copied verbatim from the author's existing Manual rationale with the author's explicit permission.

·It's baseline exit extension names only its deferred ·Owe target; ·Cheer receives solely at the x-height. The contact ledger preserves ·Cheer's shipped baseline foot when its exit is declined.

## Verification recipe

Run gates serially; detach heavy passes per `doc/running-long-steps.md`. `doc/testing.md` identifies each gate's authority and the macOS sandbox restrictions that require an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
PYTHONPATH=. uv run python rebuild/tools/probe.py E655:E65E
PYTHONPATH=. uv run python rebuild/tools/probe.py E65E:E652:E653
PYTHONPATH=. uv run python rebuild/tools/probe.py E65E:E67A:E652:E653
PYTHONPATH=. uv run python rebuild/tools/probe.py E65E:E670:E67A
PYTHONPATH=. uv run python rebuild/tools/probe.py E65E:E670:E656
make test-rebuild
make test
make artifact-cycle
uv run python -m rebuild.tools.scaling_sweep | tee rebuild/scaling-ladder.txt
uv run python -m rebuild.pipeline.coretext_smoke --font rebuild/out/m1/M1.otf
make verdict-ready
```

The ·Cheer block in `rebuild/pipeline/smoke_sequences_m1.txt` is the pair, break, ligature, yield, and boundary probe worklist. Read the `invariant` block of `rebuild/review-census-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
