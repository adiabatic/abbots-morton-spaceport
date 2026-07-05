# Verdict-application progress

The living checkpoint for turning the round-3 review verdicts into rune-data edits, one logical change at a time. Each change is made, eyeballed, and committed on its own before the next begins — commits are the real checkpoint; this file tracks what each commit accounted for, what remains, and the open questions.

> **Resume here.** Phases E1/E3 (extensions/contractions) and Phases 2–5 (seam-loss-withdrawal, the regrouping-floor-drift batch, and the blessing-pass verdict application, including the SS10 formation-suppression engine work) are **fully committed** 2026-06-29..2026-07-04 (commits `8749048`..`f7cfae0`, plus the SS03-MAYTEA and UALT1 follow-ups) — see `git log` for the per-change detail. **The one still-open task is E2 below** (the round-2 engine wall). The live migration frontier and the ongoing round-3 review adjudication live in `WHATNEXT.md`’s M1 thread; the SS03-MAYTEA and UALT1 follow-up residues are tracked there too. Workflow rules: one logical change per commit, verify each with the **full-rebuild recipe** below (not just the settle probe), show the user before committing, and keep this file honest.

## Source of truth

- **Verdicts:** `verdicts-carried-final2.json` (format `ams-review-verdicts/1`, manifest `2026-07-04T20:22:20Z`, 3,345 unique-unit verdicts: 3,175 approve / 84 reject / 8 neither / 78 either). The manifest it pins matches the live `rebuild/out/review/manifest.json`.
- **Units:** `rebuild/out/review/units/*.json` — each unit (`u-NNNN`) carries `class`, `notation`, before/after cells + seams + `extensions`, the `explain` settle trace, and a `drafts.policy` suggestion (target rune file, keypath, a concrete record). The drafts mostly frame everything as `prefer`/`refuse`, so the **review class** is the reliable signal for what a window is about, not the draft keypath.

### Verdicts are evidence, not gospel

Rune edits are **rule-level** (one `extend`/`contract`/`refuse` record with a `when:` predicate); verdicts are **window-level evidence**. A single record decision usually subsumes dozens of windows. Where a recorded verdict no longer reflects intent (a mind-change), the override is captured **here**, at the rule level — the gitignored verdict export is left untouched.

**Recorded overrides:**

- **·It·Day connector = 1px, not 2px.** ~108 windows carry `approve` for the entered-·It exit extension before ·Day (`qsIt.yaml` `extend[1]`, the `ex-ext-1` 2px connector), plus 7 `either` and 10 already-`reject`. Mind-changed to: ·It·Day reverts to the legacy single-pixel connector. Handled by change **E1** below.
- **·Tea·No = the baseline alt chain, not the x-height join (2026-07-03).** The user’s stated preference: absent other considerations, ·Tea·No should render `·Tea ~b~ ·No.alt` (full ·Tea joining alternate ·No at the baseline) rather than the old font’s default `·Tea.half ~x~ ·No`. This supersedes, at rule level: the x-height-first reading of the u-14734–14742 reject notes (“if ·Tea doesn’t join ·No at the x-height then we should use a full-size ·Tea and a ·No.alt” — the fallback clause is now the preference), the 8 trailing-pair approvals that pinned ·X·Tea·No at y5 (u-15258, u-1842, u-1848, u-1863, u-1881, u-1887, u-15496, u-15137), and the old-parity T8 shape that was briefly applied and then replaced the same day. Handled by the final Group C landing below.

## Phases (all committed except E2)

Working order was extensions/contractions first, then the larger `prefer`/`refuse`/gain piles. Every landed edit is in git and carries its rationale in the runes’ own `why:` fields; this is the one-line-per-phase map.

- **Extensions/contractions:** E1 `8749048` (·It·Day → uniform 1px connector), E3 `5ffff0e` (·May·(Day+Utter) x-height exit extension restored); `same-seam-extension-non-summing` and the `contract:` records were confirmed no-op for this batch. **E2 is the one still-open task — see below.**
- **Phase 2 — seam-loss-withdrawal:** Groups A, B, D-1, and C all committed (`e300b13` and `6ab6309`, Group C as the `·Tea ~b~ ·No.alt` alt chain); D-2 and the “6 engine-limited” park dissolved by the boundary rule + Group C’s acceptor.
- **Phase 3 — the regrouping-floor-drift batch:** N1, N4, Q1★, Q2, Q3, Q4, and P-Indian (the Manual-pin gate’s first catch) all landed.
- **Phase 4 — blessing-pass application:** DT1 `658a3c3`, DT2 `362122f`, FT1 `a60d780` (ss03 full-·Tea bar), IT1 `4589d20` (·It never joins itself), UM1, DT3 `10d71ff`, ME1 `a2c3334`.
- **Phase 5 — the everything-verdicted export:** IU1/IU2, PIT1+DID1, DTIU1, INO1, NALT-TEA, PPN1, and the SS10 formation-suppression engine work (SS10-LIG superseded by SS10-FORM) committed `26d2927..f7cfae0`; the SS03-MAYTEA full-·Tea-bar and UALT1 follow-ups landed after. Any remaining follow-up residues are carried in `WHATNEXT.md`.

## The one open task — E2 (parked)

- [ ] **E2 — `halves-entry-extension-restored` (172 rejects). PARKED (decision 2026-06-29: skip, do easy items first).** Stays documented; revisit as either the empirical sibling-deletion test (~103) or the engine milestone (~27 wall). 0 approve / 172 reject (10 identical = pixel-identical, harmless). Traced to two distinct phenomena:
  - **The round-2 engine wall (~27, ·May).** `qsMay.extend[3]` (baseline entry, the `en-ext-1`) in the `pea-may` (composed `en-ext-1`+`ex-ext-1` under ss03) and `it-may` ([X]·Oy·It·May, predecessor’s-predecessor entered) windows. `m1-divergences.yaml`’s `halves-entry-extension-restored` entry (with the lever-hunt workflow `rebuild/evidence/lever-hunt-wf.js` and its result `wf2-result.json`) records an **exhaustive, build-tested 25-candidate lever hunt that found no closed-vocabulary rune record can revert these without collateral** — the discriminator is a depth-2 left-context predicate the frozen `when:` grammar cannot express. Honoring these = the deferred **extension-suppression engine milestone**, not a rune edit.
  - **The x-height-halves siblings (~103, NEW).** `qsDay.extend[0]`, `qsOy.extend[0]`, `qsNo.extend[0]`, `qsPea.extend[0]`, `qsMay.extend[4]` — all the `{entry: x-height, by: 1, when: {left: {class: halves-that-exit-at-x-height, joined_at: x-height}}}` shape. These are the **same transcription-artifact record round-1 deleted from `qsIt`** (“the shipped font never realized it”); these siblings just weren’t reached, and ~103 of them only surfaced as divergent after the 2026-06-27 pulled-back re-baseline, so they **predate the round-2 analysis**. **Hypothesis: cleanly deletable parity-revert.** Unconfirmed — needs an empirical oracle test (delete each record on a scratch build via `rebuild/tools/scratch_build.py`, confirm UNMATCHED drops with zero collateral on currently-matched windows) before any deletion, because the review surface contains only divergent windows and can’t by itself prove a record never fires in a matched one.

## Gates / known state

- **Settle probe** (per-change spot check): see commands below.
- **Rebuild suite:** `uv run pytest rebuild/ -n auto --dist worksteal -q`. A clean HEAD fails only the **4 documented batch-1 spec pins** (`test_surface::test_real_cell_bindings_all_match`, `test_spec_load::{test_loads_all_six_runes, test_predicate_class_membership, test_group_resolution}`) — those pin the M1-batch-1 spec shape (six runes, batch-1 predicate classes) and predate this pass; re-baseline them when the batch-2 migration formally closes. A rune edit also trips the artifact-pinned review-census tests until `run_m1` + `review.build` re-run — for live census numbers run `uv run python -m rebuild.review.census --check` rather than reading counts here. Distinguish real regressions by diffing the failure set against this clean-HEAD baseline.
- **`make test`** (main font, `test/ site/`) does not read the rune files; runs green independently.
- **The Manual-pin gate:** `rebuild/pipeline/manual_pins.py` replays every corpus data-expect pin whose text falls inside the migrated alphabet against the built M1.otf — trait assertions resolve through stance `traits:` in the spec, **no waiver channel** (a disagreement means either the runes break a Manual mandate, or the pin mistranscribes The Manual — fix whichever is wrong). Wired twice: a hard gate in `run_m1.main()` (writes `manual_pins_summary.json`; its `top_blocking_letters` is batch-prioritization signal), and `rebuild/test_manual_pins.py` in the rebuild suite. Artifact-pinned like the review census tests: a rune edit trips it until `run_m1` recompiles `rebuild/out/m1/M1.otf`.
- **Space and ZWNJ rows are absent from UNMATCHED** — the ratified boundary-equals-word-boundary rule absorbs every non-position row whose window contains a run-splitting boundary (space or ZWNJ) into the `boundary-echo` ledger class at top precedence, so per-config UNMATCHED diffs against pre-2026-07-03 audits will show those windows vanishing; that is the rule working, not a regression. `run_m1.main()` also runs the boundary-equals-text-edge split-buffer gate before the oracle.
- **Collateral verification harness:** `rebuild/tools/seam_loss_diff.py <candidate_runes_dir>` re-settles the real runes and a candidate dir under the default config and reports per-group target restoration + any approved-window movement (real collateral). `rebuild/tools/seam_loss_probe.py` is the older shard-comparison probe — prefer the differ; the shard `after` seams are config-shadowed and produce phantom regressions.

## Resume commands

```bash
# Verdict ↔ unit ↔ class/draft join (counts by class, by keypath, by right-neighbor):
python3 - <<'PY'
import json, glob, collections
units={}
for f in glob.glob('rebuild/out/review/units/*.json'):
    for u in json.load(open(f)): units[u['id']]=u
V=json.load(open('verdicts-carried-final2.json'))['verdicts']
vbyu={}
for v in V:
    uid=v['unit']
    if uid not in vbyu or v['at']>vbyu[uid]['at']: vbyu[uid]=v
tab=collections.defaultdict(collections.Counter)
for uid,v in vbyu.items():
    u=units.get(uid); cls=u['class'] if u else '<MISSING>'
    tab[cls][v['verdict']]+=1
for cls in sorted(tab, key=lambda c:-sum(tab[c].values())):
    print(f'{cls:40s} {dict(tab[cls])}')
PY

# Per-change settle probe (edit the cases dict to the windows under review):
PYTHONPATH=. uv run python - <<'PY'
from rebuild.review.enrich import load_spec
from rebuild.pipeline.settle import settle, cell_label
spec = load_spec()
cases = {"ZWNJ Tea It Day":[0x200C,0xE652,0xE670,0xE653], "Tea It It":[0xE652,0xE670,0xE670]}
for label, cps in cases.items():
    print(f"{label:24s} -> {[cell_label(spec, s.cell) for s in settle(spec, cps, frozenset())]}")
PY

# Reflow a rune to canonical flow-vs-block formatting after editing:
uv run python tools/reflow_yaml.py glyph_data/runes/qsIt.yaml
```

### Full-rebuild verification (the real arbiter — the settle probe alone is not enough)

The served review surface (`rebuild/out/review/`, port 7294) is built from a **pre-compiled font** (`rebuild/out/m1/M1.otf`) plus **frozen unit shards**, so it never reflects an uncommitted or just-committed rune edit. The settle probe shows cell labels but not the rendered pixels or the oracle match/unmatched verdict. To truly verify a change (and catch divergences-from-old the probe hides), rebuild and diff:

```bash
uv run python -m rebuild.pipeline.run_m1                       # recompile M1.otf from the runes (writes rebuild/out/m1/, ~minutes)
uv run python -m rebuild.review.build --out tmp/review-preview # rebuild the surface non-destructively (leaves :7294 + verdicts untouched)
(cd tmp/review-preview && python3 -m http.server 7295)         # eyeball at http://localhost:7295/#class=...&unit=u-NNNN
```

Gotcha: `run_m1` exits nonzero whenever unmatched rows exist (i.e., always, mid-migration), so `run_m1 && review.build` silently skips the surface rebuild — run the two commands separately and check that `review.build` actually printed its `Wrote …` line before diffing.

Then compare UNMATCHED windows per config, keyed by codepoints (stable across rebuilds), preview vs the served surface:

```bash
python3 - <<'PY'
import json, glob, collections
def load(d):
    us={}
    for f in glob.glob(d+'/units/*.json'):
        for u in json.load(open(f)): us[u['codepoints']]=u
    return us
old, new = load('rebuild/out/review'), load('tmp/review-preview')
def bycfg(us):
    c=collections.Counter()
    for u in us.values():
        for cfg,cc in (u.get('config_classes') or {}).items():
            if cc=='UNMATCHED': c[cfg]+=1
    return c
co, cn = bycfg(old), bycfg(new)
for cfg in sorted(set(co)|set(cn)): print(f'{cfg:16s} {co[cfg]:4d} -> {cn[cfg]:4d}  {cn[cfg]-co[cfg]:+d}')
PY
```

**Lesson banked (2026-06-29):** the **stable old-font connector for a window lives in the config’s `before` field, and it varies by config** — reading `before.glyphs` across mixed configs led me to a false “87 regressions” alarm for E1; the per-config rebuild diff is what set it straight (the true number was +1). Always confirm a divergence-from-old with the rebuild diff, per config, not the raw `before` field.

## Keeping this file honest

Update this file in the same change that lands a rune edit: tick the checklist item, note the commit, record any new override or follow-up. `WHATNEXT.md`’s M1 thread links here once the first edit commits.
