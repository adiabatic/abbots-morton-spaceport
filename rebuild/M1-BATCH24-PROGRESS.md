# M1 batch 24 — qsShe

Scratch for the ·She migration. Delete when the sitting closes the batch, lifting any surviving forward pointer into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsShe.yaml` awaits the author's vet; its `# DRAFT` marker is the worklist.
- The new review units await their sitting. `make verdict-ready` is the readiness authority.

## Recorded design overrides

The author chooses one motion with a join-specific foot. The curved foot is the isolated drawing; the flattened-bottom bitmap binds the baseline-exiting cell.

The yielding preference before ·It preserves the shipped `·She | ·It ~b~ ·Day.half` grouping, including the `·Day+Utter.half` continuation. The ss04 capability keeps both baseline joins.

The absolute ·It preference after ·See and before ·No·Eight holds the Manual pin that includes ·She. Its `why:` in `glyph_data/runes/qsIt.yaml` is the author's rationale; the ordinary entered-·It chains keep their existing behavior.

## Verification recipe

Run gates serially; detach heavy passes per `doc/running-long-steps.md`. `doc/testing.md` identifies each gate's authority and the macOS sandbox restrictions that require an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
PYTHONPATH=. uv run python rebuild/tools/probe.py E65C:E652:E653
PYTHONPATH=. uv run python rebuild/tools/probe.py E65C:E67A:E652:E653
PYTHONPATH=. uv run python rebuild/tools/probe.py E65C:E670:E67A
PYTHONPATH=. uv run python rebuild/tools/probe.py E65A:E670:E666:E673
make test-rebuild
make test
make artifact-cycle
uv run python -m rebuild.tools.scaling_sweep | tee rebuild/scaling-ladder.txt
uv run python -m rebuild.pipeline.coretext_smoke --font rebuild/out/m1/M1.otf
```

The ·She block in `rebuild/pipeline/smoke_sequences_m1.txt` is the pair, break, ligature, yield, and boundary probe worklist. Read the `invariant` block of `rebuild/review-census-pins.json` before accepting its generated diff.

Run `make verdict-ready` after starting the review server; a green artifact cycle leaves the server stopped when it refreshes the surface.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
