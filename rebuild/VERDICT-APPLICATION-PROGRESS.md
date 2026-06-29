# Verdict-application progress

The living checkpoint for turning the round-3 review verdicts into rune-data edits, one logical change at a time. Each change is made, eyeballed, and committed on its own before the next begins — commits are the real checkpoint; this file tracks what each commit accounted for, what remains, and the open questions.

> **Resume here (2026-06-29).** This is an in-flight pass _within_ the M1 round-3 adjudication — finish it before resuming WHATNEXT.md's main migration thread. **The extensions/contractions phase is COMPLETE** (E1 `8749048`, E3 `5ffff0e`; E2 parked; same-seam + contractions no-op). **Next up: Phase 2** — start with `seam-loss-withdrawal` (83 rejects, the pulled-back aftermath); see "Phase 2 candidates" below. Workflow rules: one logical change per commit, verify each with the **full-rebuild recipe** (not just the settle probe — see "Resume commands"), show the user before committing, and keep this file honest. Working style the user likes: scope each edit tightly, prove it surgical, present pros/cons, don't commit without approval.

## Source of truth

- **Verdicts:** `verdicts-11.33.00PM.json` (format `ams-review-verdicts/1`, manifest `2026-06-29T04:45:25Z`, 4,828 unique-unit verdicts: 4,146 approve / 332 reject / 318 neither / 20 either / 12 identical). The manifest it pins matches the live `rebuild/out/review/manifest.json`.
- **Units:** `rebuild/out/review/units/*.json` — each unit (`u-NNNN`) carries `class`, `notation`, before/after cells + seams + `extensions`, the `explain` settle trace, and a `drafts.policy` suggestion (target rune file, keypath, a concrete record). The drafts mostly frame everything as `prefer`/`refuse`, so the **review class** is the reliable signal for what a window is about, not the draft keypath.

### Verdicts are evidence, not gospel

Rune edits are **rule-level** (one `extend`/`contract`/`refuse` record with a `when:` predicate); verdicts are **window-level evidence**. A single record decision usually subsumes dozens of windows. Where a recorded verdict no longer reflects intent (a mind-change), the override is captured **here**, at the rule level — the gitignored verdict export is left untouched.

**Recorded overrides:**

- **·It·Day connector = 1px, not 2px.** ~108 windows carry `approve` for the entered-·It exit extension before ·Day (`qsIt.yaml` `extend[1]`, the `ex-ext-1` 2px connector), plus 7 `either` and 10 already-`reject`. Mind-changed to: ·It·Day reverts to the legacy single-pixel connector. Handled by change **E1** below.

## Phases

Working order (user's call): **extensions/contractions first** (most inconsistent, cheapest YAML), then the larger `prefer`/`refuse`/gain piles.

The extension/contraction story is concentrated in three review classes plus one cross-cutting record:

| Class | windows | approve | reject | other | meaning |
| --- | ---: | ---: | ---: | --- | --- |
| `halves-entry-extension-restored` | 183 | 1 | 172 | 10 identical | receivers (·Pea/·May/·Day/…) extending their **entry** for a half that exits at x-height — overwhelmingly rejected |
| `same-seam-extension-non-summing` | 88 | 88 | 0 | — | keep as-is |
| `extension-non-summing` | 30 | 20 | 10 | — | mixed (·Tea+Oy→·Day keep; ·May·Day·Utter reject) |
| _cross-cutting_ `qsIt` `extend[1]` (entered-·It exit extension) | — | see below | — | — | fires before ·Day/·It/·May/·Utter/·No; votes differ sharply by right-neighbor |

`qsIt` `extend[1]` firings by right-neighbor (windows with a verdict): ·Day 108a/10r/7e · ·It 93a/2r · ·May 89a/0r · ·Utter 63a/12r/48n/6e · ·No 1a · qsDay_qsUtter 4a/1r/1e.

## Extensions/contractions checklist

- [x] **E1 — ·It·Day single-pixel connector. COMMITTED `8749048`.** Narrow `glyph_data/runes/qsIt.yaml` `policy.extend[1]` with `right: {except: [family: qsDay, family: qsDay_qsUtter]}` so the entered-·It exit extension no longer fires before ·Day **or the ·Day+·Utter ligature** (same half-·Day baseline seam) — 1px connector — while ·It·It / ·It·May / ·It·Utter keep the 2px extension. **Verified by full rebuild** (see recipe below): net +1 UNMATCHED window in default/ss02/ss05, zero elsewhere. E1 makes ·It·Day **uniformly** 1px — a _deliberate divergence from the old font_ in the one window where the old font drew 2px (`u-14916` ◊ZWNJ·May·It·Day, ·It entered at x-height post-ZWNJ): **user-confirmed LGTM 2026-06-29, keep uniform 1px.** That window + its boundary siblings are intended divergence to bless at the next re-baseline, not a regression.
- [ ] **E2 — `halves-entry-extension-restored` (172 rejects). PARKED (decision 2026-06-29: skip, do easy items first).** Stays documented; revisit as either the empirical sibling-deletion test (~103) or the engine milestone (~27 wall). 0 approve / 172 reject (10 identical = pixel-identical, harmless). Traced to two distinct phenomena:
  - **The round-2 engine wall (~27, ·May).** `qsMay.extend[3]` (baseline entry, the `en-ext-1`) in the `pea-may` (composed `en-ext-1`+`ex-ext-1` under ss03) and `it-may` ([X]·Oy·It·May, predecessor's-predecessor entered) windows. `m1-divergences.yaml`'s `halves-entry-extension-restored` entry + `POLICY-ROUND-2-REPORT.md` record an **exhaustive, build-tested 25-candidate lever hunt that found no closed-vocabulary rune record can revert these without collateral** — the discriminator is a depth-2 left-context predicate the frozen `when:` grammar cannot express. Honoring these = the deferred **extension-suppression engine milestone**, not a rune edit.
  - **The x-height-halves siblings (~103, NEW).** `qsDay.extend[0]`, `qsOy.extend[0]`, `qsNo.extend[0]`, `qsPea.extend[0]`, `qsMay.extend[4]` — all the `{entry: x-height, by: 1, when: {left: {class: halves-that-exit-at-x-height, joined_at: x-height}}}` shape. These are the **same transcription-artifact record round-1 deleted from `qsIt`** ("the shipped font never realized it"); these siblings just weren't reached, and ~103 of them only surfaced as divergent after the 2026-06-27 pulled-back re-baseline, so they **predate the round-2 analysis**. **Hypothesis: cleanly deletable parity-revert.** Unconfirmed — needs an empirical oracle test (delete each record on a scratch build via `tmp/scratch_build.py`, confirm UNMATCHED drops with zero collateral on currently-matched windows) before any deletion, because the review surface contains only divergent windows and can't by itself prove a record never fires in a matched one.
- [x] **E3 — ·May·(Day+Utter) x-height exit extension restored.** The 10 `extension-non-summing` rejects are all ·May·Day·Utter (`ext=0`, decided by order, no record): the old font drew `qsMay.ex-ext-1` but the new font dropped it because `qsMay.extend[0]`'s trigger list had bare `qsDay` (and `qsFee/qsJai/qsJay/qsRoe/qsIt`) but **not** the `qsDay_qsUtter` ligature. Fixed by adding `qsDay_qsUtter` to that list (code-point order, right after bare `qsDay`) — the mirror of E1's ligature point. Verified: ·May·(Day+Utter) and its follower variants regain `ex-ext-1`; bare ·May·Day unchanged. The 20 approves (·Tea+Oy→·Day etc.) are a different record, untouched. **COMMITTED `5ffff0e`.** Trips two expected baseline pins (`test_review_enrich`, `test_review_build::test_secondary_seam_census` −6 units) that clear on the phase-end review rebuild.
- [x] **`same-seam-extension-non-summing` (88 approve) — NO-OP confirmed.** All approve; the engine's behavior is liked, no record changes.
- [x] **Contractions sweep — NO-OP this batch.** No unit cites any `policy.contract` record and no cell carries a contraction marker: every `contract:` trigger (·J'ai/·Zoo/·Jay/·Fee on `qsNo`/`qsUtter`/`qsTea`/`qsMay`) is an **unmigrated family**, so the records can't fire in the M1-batch-2 corpus. Re-adjudicate when those families migrate.

**Extensions/contractions phase: COMPLETE** (2026-06-29) — E1 + E3 committed, E2 parked, same-seam + contractions no-op.

## Phase 2 candidates (verdict piles outside extensions)

Reject-heavy / mixed (need rune work): `seam-loss-withdrawal` (59a / **83r** — the 2026-06-27 pulled-back-removal aftermath, the biggest actionable reject pile). Mostly-neither: `regrouping-floor-drift` (24n), `deferred-ss04` (44r but ssNN-parked). Approve-heavy (likely confirm-only no-ops): `no-chain-gains` 160, `kern-channel-out-of-scope` 187, `may-utter-gains` 86, `ss03-chain-join-gains` 70, `entered-it-baseline-join-gain` 87, `tea-it-xheight` 39, `pea-chain-regularized` 37, `oy-it-baseline` 20.

## Open follow-ups / questions

- ~~**qsDay_qsUtter ligature after ·It.**~~ Resolved in E1: the exclusion covers both `qsDay` and the `qsDay_qsUtter` ligature.
- After the extension phase, **rebuild the review surface** (`uv run python -m rebuild.review.build`) to re-baseline; that clears the expected `test_review_enrich::test_derived_cells_match_the_audit` divergence and re-classifies the now-matched windows.

## Gates / known state

- **Settle probe** (per-change spot check): see commands below.
- **Rebuild suite:** `uv run pytest rebuild/ -n auto --dist worksteal -q`. Baseline on a clean tree = **4 pre-existing failures** (`test_surface::test_real_cell_bindings_all_match`, `test_spec_load::{test_loads_all_six_runes, test_predicate_class_membership, test_group_resolution}`) — unrelated to this work. Any rune edit also trips `test_review_enrich::test_derived_cells_match_the_audit` until the review surface is rebuilt; that one is **expected**, not a regression.
- **`make test`** (main font, `test/ site/`) does not read the rune files; runs green independently.

## Resume commands

```bash
# Verdict ↔ unit ↔ class/draft join (counts by class, by keypath, by right-neighbor):
python3 - <<'PY'
import json, glob, collections
units={}
for f in glob.glob('rebuild/out/review/units/*.json'):
    for u in json.load(open(f)): units[u['id']]=u
V=json.load(open('verdicts-11.33.00PM.json'))['verdicts']
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

**Lesson banked (2026-06-29):** the **stable old-font connector for a window lives in the config's `before` field, and it varies by config** — reading `before.glyphs` across mixed configs led me to a false "87 regressions" alarm for E1; the per-config rebuild diff is what set it straight (the true number was +1). Always confirm a divergence-from-old with the rebuild diff, per config, not the raw `before` field.

## Keeping this file honest

Update this file in the same change that lands a rune edit: tick the checklist item, note the commit, record any new override or follow-up. `WHATNEXT.md`'s M1 thread links here once the first edit commits.
