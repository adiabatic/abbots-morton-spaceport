# M1 batch 23 — qsZoo and qsThey_qsZoo

Scratch for the ·Zoo migration. Delete when the sitting closes the batch, lifting any surviving forward pointer into `WHATNEXT.md`.

## Parked

- The drafted ductus in `qsZoo.yaml` and `qsThey_qsZoo.yaml` awaits the author's vet; their `# DRAFT` markers are the worklist.
- The new review units await their sitting. `make verdict-ready` is the readiness authority.

## Recorded design overrides

No new user design overrides are recorded. The contraction records transcribe the shipped font's placed ink: half-·Pea's overlap is removed from ·Zoo's crown, and half-·Tea's anchor tuck is expressed on ·Zoo's entry. The verification compares the union of both letters' placed pixels, since an overlapping pixel belongs to either drawing without changing the pair.

The `zoo-entry-contraction-respelled` ledger class covers the ·Tea·Zoo contraction's form-name difference. Its predicate excludes the unrelated ·It·Roe redraw that can change ink without moving origins or advances; the position channel checks the remaining candidates for unrelated shifts.

The yielding preference for `·Zoo | ·No ~x~ ·Et` transcribes the old alternate ·No's exclusion before ·Et. The `·Tea ~b~ ·May ~x~ ·Zoo` Manual example retains its pinned joins; compatibility modifiers are interpreted by `rebuild.pipeline.manual_pins`.

The unformed ·Zoo declines its baseline exit after ·They so the formation guard preserves the shipped font's `·They+Zoo` ligature before every follower. The ligature itself has no external anchors.

The ·It preference after ·Zoo preserves the old `·Zoo | ·It ~b~ ·Utter` grouping and the old follower exceptions on ·Utter. Its scope is recorded in `qsIt.yaml`; the ss04 both-baseline pairing remains available under the existing ruling there.

## Verification recipe

Run gates serially; detach heavy passes per `doc/running-long-steps.md`. `doc/testing.md` identifies each gate's authority and the macOS sandbox probe that requires an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
PYTHONPATH=. uv run python rebuild/tools/probe.py E650:E65B
PYTHONPATH=. uv run python rebuild/tools/probe.py E652:E65B
PYTHONPATH=. uv run python rebuild/tools/probe.py E65B:E666:E672
PYTHONPATH=. uv run python rebuild/tools/probe.py E652:E665:E65B
PYTHONPATH=. uv run python rebuild/tools/probe.py E65B:E652:E653
PYTHONPATH=. uv run python rebuild/tools/probe.py E65B:E67A:E652:E653
make test-rebuild
make test
make artifact-cycle
make verdict-ready
uv run python -m rebuild.tools.scaling_sweep | tee rebuild/scaling-ladder.txt
```

The ·Zoo block in `rebuild/pipeline/smoke_sequences_m1.txt` is the joining-pair, break, ligature, and boundary probe worklist. Read the `invariant` block of `rebuild/review-census-pins.json` before accepting its generated diff.

## Resume

```zsh
make verdict-ready
make review-cycle
```
