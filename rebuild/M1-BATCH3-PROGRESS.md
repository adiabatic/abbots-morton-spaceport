# M1 batch 3 progress — qsLow

Scratch progress file for the in-flight qsLow migration; delete at batch close, lifting surviving forward-pointers into WHATNEXT.md.

## Committed

- 878709b Add ·Low
- 2f18041 Don’t prefer ·Day+Utter so hard that you don’t get ·Day·Utter.alt·Low like The Manual uses

The artifact cycle has re-run at 2f18041 (all gates green); its fallout — the re-pinned census, the subset-table row-count re-baseline, and the carried-master swap to `rebuild/evidence/verdicts-carried-2f18041.json` — sits in the working tree awaiting commit.

## Parked

- **Verdict rows for the next sitting** — 10,653 unmatched oracle rows, all verdict-gated taste: the 6,564 batch-2 leftovers, the fresh qsLow families (Pea·No regrouping with ·Low appended, alt-·Utter join gains, the ratified ·It exit-extension carve-out before ·Day, ss03 chain gains), and the ·Utter-bearing rows the fork resolution moved — 237 old formation-fork rows now match the shipped font, and the additions are alt-·Utter tie-flips (bare ·Utter carrying its baseline exit at ties the old font left to `order:`) plus unformed-chain gains where formation now yields. None is a qsLow authoring defect.
- **`why:` for qsUtter.policy.prefer[2]** — the §5.9 follower one-liner (`{cell: {exit: baseline}, over: {entry: baseline}}`) is a design decision whose rationale is the user’s to record; the `why:` text is pending.
- **Ductus sign-off** — qsLow’s drafted `hapax` way carries `# DRAFT — pending author sign-off` (the letter had no ductus prose in `glyph_data/quikscript.yaml`).

## Design overrides

- **§5.7 late formation is built** (was: designed but unbuilt). Formation yields per window iff the unformed trailing component could realize a seam toward the follower and the formed ligature could realize none, under every capability configuration — derived from join surfaces, no per-ligature data. Emitted as the `m1_formation_guarded` chaining-context lookup with generated `ignore sub` rows (a sanctioned exemption alongside the namer-dot guard). Design doc §5.7, the §7 formation row, and honest-criticism item 8 are updated in place.
- **qsUtter gains the §5.9 follower prefer** — not a transcription of an old-YAML record; it re-expresses the old engine’s pre-flip behavior (alt engaged toward baseline-only-reachable followers) as the designed cell-grain one-liner. A `when:`-scoped variant was tried and is structurally rejected: any axis constraint makes it cross (not nest) with `policy.prefer[0]` and settlement raises E-AMBIGUOUS, so the unconditioned §5.9 shape is the only conflict-free form. The rune schema now permits when-less prefer records for exactly this idiom.

## Verification recipe

- `uv run pytest rebuild/test_spec_load.py rebuild/test_surface.py -n auto --dist worksteal` — expect exactly the four documented baseline failures.
- `uv run python -m rebuild.pipeline.run_m1 --jobs 8` — defect/boundary/Manual-pin gates pass (pin gate 29/29); exits nonzero at the oracle stage on the verdict-gated rows, expected until the sitting lands verdicts.
- Oracle direct: `uv run python -c "from rebuild.pipeline import run_m1; run_m1.run_oracle(spec=run_m1.load_default_spec(), jobs=8)"` — expect `multi_matched: 0`, `unmatched: 10653`.
- `uv run python -m rebuild.pipeline.run_m1 --conform-only --jobs 8` — passes (271,452 sequences, 0 divergences, 0 uncovered rules/transitions).
- `make test` and `uv run pytest rebuild/ -n auto --dist worksteal`; the shipped legacy pipeline is untouched by the rebuild work.

## Resume

1. Commit the artifact-cycle fallout; record the qsUtter `why:`.
2. Review sitting: `make review-serve`, import `rebuild/evidence/verdicts-carried-2f18041.json`, then `verdicts-echo-fill.json`, then work `http://localhost:7294/docket.html` (re-bake with `uv run python rebuild/tools/review_docket.py rebuild/evidence/verdicts-carried-2f18041.json` if the surface changes).
3. Sign off qsLow’s ductus (grep `# DRAFT`).
