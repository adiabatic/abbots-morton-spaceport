"""Build rebuild/out/policy-round-1-assertions.json — Phase 3 acceptance assertion inventory for the round-1 verdict application (reconnaissance task B)."""
import json, glob, collections, datetime

units = {}
for f in glob.glob('rebuild/out/review/units/*.json'):
    for u in json.load(open(f)):
        units[u['id']] = u

verdict_export = json.load(open('verdicts-10.42.06PM.json'))
vmap = {x['unit']: x for x in verdict_export['verdicts']}
manifest = json.load(open('rebuild/out/review/manifest.json'))
assert verdict_export['manifest_generated_at'] == manifest['generated_at']

MACHINE = {'zwnj-word-initial-unification', 'dangling-anchor-dropped', 'bare-name-live-join'}
human = {uid: u for uid, u in units.items() if u['class'] not in MACHINE}

buckets = {'rejected': [], 'approved': [], 'either': [], 'skip': [], 'neither': [], 'unverdicted': []}
for uid, u in sorted(human.items()):
    verdict = vmap.get(uid)
    if verdict is None:
        buckets['unverdicted'].append(uid)
    else:
        buckets[{'approve': 'approved', 'reject': 'rejected'}.get(verdict['verdict'], verdict['verdict'])].append(uid)

configs = manifest['configs']

def windows_by_config(uids):
    per = {c: sorted({units[uid]['codepoints'] for uid in uids if c in units[uid]['configs']}) for c in configs}
    return {c: w for c, w in per.items() if w}

def unit_index(uids):
    return {uid: {'class': units[uid]['class'], 'batch': units[uid]['batch'], 'codepoints': units[uid]['codepoints'], 'configs': units[uid]['configs'], 'verdict': vmap[uid]['verdict'] if uid in vmap else None, 'note': (vmap[uid]['note'] or None) if uid in vmap else None} for uid in uids}

doc = {
    'format': 'ams-policy-round-1-assertions/1',
    'generated_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'verdict_export': {'file': 'verdicts-10.42.06PM.json', 'exported_at': verdict_export['exported_at'], 'manifest_generated_at': verdict_export['manifest_generated_at']},
    'manifest': {'file': 'rebuild/out/review/manifest.json', 'generated_at': manifest['generated_at'], 'repo_head': manifest['repo_head'], 'before_sha256': manifest['fonts']['before']['sha256'], 'after_sha256': manifest['fonts']['after']['sha256']},
    'configs': configs,
    'semantics': {
        'rejected': 'Every window must shape identically to the baseline table (rebuild/out/baseline-<config>.tsv.gz) under every listed configuration after the round-1 policy edits: the M1 divergence flips back.',
        'approved': 'Every window must shape identically to the M1 build (the verdicted manifest, after-font SHA above) under every listed configuration: the round-1 edits must not disturb approved outcomes.',
        'either': 'Report any change from the verdicted M1 outcome; both the baseline and the M1 behavior are acceptable, so a flip is not a failure, but it must be surfaced. The per-unit any-of pin candidates enumerate the acceptable renderings.',
        'skip': 'No recorded verdict; resolutions are PROPOSED in rebuild/recon/policy-round-1-reconcile.md and await user confirmation. Report any change; never auto-record.',
        'neither': 'User judged both behaviors wrong; carried on the follow-up ledger. Report any change.',
        'unverdicted': 'No verdict was ever given (triage stopped at 441 of 725). Count and report windows whose rows change (siblings of rejected behavior may legitimately re-converge with baseline); never treat a change here as approval or rejection.',
    },
    'contradiction_flags': {
        'u-0280': "Recorded reject; the user's note ('I think I would rather prefer ·Tea·It to join ') is satisfied by flipping to baseline, but the unit-local drafted policy record (qsOy prefer exit none before qsTea) must not be applied verbatim — see rebuild/recon/policy-round-1-reconcile.md §1.",
        'u-0468': 'Recorded approve inside the otherwise-rejected halves-entry-extension-restored class; its window carries the en-ext-1 entry-extension ink that the class revert removes, so a broad revert will change this approved window. Expected-to-change exception pending user confirmation — see rebuild/recon/policy-round-1-reconcile.md §1.',
    },
    'watch': {
        'description': 'Approved or either windows whose M1 outcome gained entry-extension ink relative to baseline (more after-cells with en-ext-* than before-glyphs with .en-ext-*). The round-1 halves-extension revert must be scoped so extensions at newly formed seams (approved join gains, notably 28 of the 40 ss03-chain-join-gains approvals) survive; only extensions at seams that already existed in baseline at the same Y were rejected. Phase 3 should diff these windows individually.',
        'units': {},
    },
    'assertions': {},
}

for uid, x in sorted(vmap.items()):
    u = units[uid]
    if x['verdict'] not in ('approve', 'either'):
        continue
    gained = sum(1 for c in u['after']['cells'] if 'en-ext-' in c) > sum(1 for g in u['before']['glyphs'] if '.en-ext-' in g)
    if gained:
        doc['watch']['units'][uid] = {'verdict': x['verdict'], 'class': u['class'], 'codepoints': u['codepoints'], 'configs': u['configs']}

for name, uids in buckets.items():
    doc['assertions'][name] = {
        'unit_count': len(uids),
        'units': unit_index(uids),
        'windows_by_config': windows_by_config(uids),
    }

with open('rebuild/out/policy-round-1-assertions.json', 'w') as fh:
    json.dump(doc, fh, indent=1, ensure_ascii=False)
    fh.write('\n')

for name, uids in buckets.items():
    wc = windows_by_config(uids)
    print(name, len(uids), 'units;', {c: len(w) for c, w in wc.items()})
