# M1 batch 26 — qsJay and qsJay_qsUtter

Scratch for the ·Jay migration. Delete when the sitting closes the batch, lifting any surviving forward pointer into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsJay.yaml` and `glyph_data/runes/qsJay_qsUtter.yaml` await the author's vet; their `# DRAFT` markers are the worklist.
- The new review units await their sitting. `make verdict-ready` is the readiness authority.
- Issue #208 (re-adjudicate qsTea's qsJay-keyed contract) and issue #211 (qsIt's dead-listed x-height refusal) are discharged by the records below; closing them and deleting the `waits on ·Jay` label are GitHub mutations on a public repo and wait for the author.

## Recorded design overrides

The old half-·Tea exit contraction before ·Jay is a tuck, as it was before ·Zoo and before ·J'ai: the compiled `qsTea.half.ex-y5.ex-con-1` keeps its whole stroke and only its anchor moves in. The rune re-spells it as ·Jay's own one-pixel entry contraction after ·Tea, and the qsTea-side record is gone. The `jai-entry-contraction-respelled` ledger class carries the name-grain rows, since the phenomenon and the crown are the same as ·J'ai's.

·Jay's entry contraction after ·Pea and after ·He, and its entry extension after ·It, yield to its exit extension before ·I, ·Ye, and ·Exam; the old font drops the entry adjustment in those windows. The entry contraction after ·Tea persists under the same exit extension. The ·Jay·Utter ligature keeps every entry adjustment under its exit extension.

·It after ·Jay refuses a baseline entry unless it also exits, so the pair breaks at default and the ss04 baseline pass-through still forms (`·Jay ~b~ ·It ~b~ ·Day`, `·Jay ~b~ ·It ~b~ ·Utter`). The live x-height refusal after ·Jay covers the other entered pairing.

·It keeps an x-height entry over an x-height exit into ·Jay or ·Jay·Utter, the yielding preference that holds the Manual's `·Pea.half ~x~ ·It | ·Jay+Utter ~x~ ·No ~x~ ·Zoo` pin and matches every old-font `·X ~x~ ·It | ·Jay` window. The same windows before ·J'ai and ·Cheer already diverge the other way and stay as they are; widening the preference to them is the author's call.

The ligature's follower scope is authored from the baseline windows: it names ·Zoo, which the ligature joins at the x-height, and omits ·At, ·See, and ·Ooze, which un-form or break.

The yielding preference before ·Tea follows the existing qsJai record.

## Verification recipe

Run gates serially; detach heavy passes per `doc/running-long-steps.md`. `doc/testing.md` identifies each gate's authority and the macOS sandbox restrictions that require an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py E650:E65F E652:E65F E65A:E652:E65F E670:E65F E665:E65F E651:E665:E65F E650:E65F:E675 E652:E65F:E675 E670:E65F:E675 E65F:E652 E65F:E652:E653 E65F:E67A:E652:E653 E65F:E670 E65F:E670:E653 E65F:E670:E67A E65F:E67A E650:E65F:E67A E670:E65F:E67A E65F:E67A:E651 E65F:E67A:E65D E65F:E67A:E652 E65F:E67A:E656 E65F:E668 E65F:E653 E65F:E675
make test-rebuild
make test
make artifact-cycle
uv run python -m rebuild.tools.scaling_sweep | tee rebuild/scaling-ladder.txt
uv run python -m rebuild.pipeline.coretext_smoke --font rebuild/out/m1/M1.otf
make verdict-ready
```

The ·Jay block in `rebuild/pipeline/smoke_sequences_m1.txt` is the pair, break, ligature, yield, and boundary probe worklist. Read the `invariant` block of `rebuild/review-census-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
