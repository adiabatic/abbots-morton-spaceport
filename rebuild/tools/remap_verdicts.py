import json, os, sys, datetime
from collections import Counter

OLD_DIR = "tmp/review-pre-pulledback"
NEW_DIR = "rebuild/out/review"
VERDICTS = "verdicts-10.53.42AM.json"
OUT = "verdicts-10.53.42AM.remapped.json"

def load_units(d):
    m = json.load(open(os.path.join(d, "manifest.json")))
    units = {}
    for c in m["classes"]:
        for u in json.load(open(os.path.join(d, c["shard"]))):
            units[u["id"]] = u
    return m, units

def key_of(u):
    b = u.get("before") or {}
    a = u.get("after") or {}
    return json.dumps([
        u.get("codepoints"),
        b.get("glyphs"), b.get("seams"),
        a.get("cells"), a.get("seams"),
    ], sort_keys=True)

old_m, old_u = load_units(OLD_DIR)
new_m, new_u = load_units(NEW_DIR)

old_uid_to_key = {uid: key_of(u) for uid, u in old_u.items()}
new_key_to_uid = {}
dup = 0
for uid, u in new_u.items():
    k = key_of(u)
    if k in new_key_to_uid: dup += 1
    new_key_to_uid.setdefault(k, uid)

v = json.load(open(VERDICTS))
carried, dropped, unfound = [], [], []
out_records = []
for rec in v["verdicts"]:
    uid = rec["unit"]
    if uid not in old_uid_to_key:
        unfound.append(rec); continue
    k = old_uid_to_key[uid]
    new_uid = new_key_to_uid.get(k)
    if new_uid is None:
        old = old_u[uid]
        dropped.append((rec, old)); continue
    nr = dict(rec); nr["unit"] = new_uid
    out_records.append(nr); carried.append((rec, uid, new_uid))

out = {
    "format": v["format"],
    "manifest_generated_at": new_m["generated_at"],
    "exported_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"),
    "remapped_from": {"file": VERDICTS, "old_manifest_generated_at": v.get("manifest_generated_at")},
    "verdicts": out_records,
}
json.dump(out, open(OUT,"w"), indent=2, ensure_ascii=False)

print(f"INPUT verdicts: {len(v['verdicts'])}  ({dict(Counter(r['verdict'] for r in v['verdicts']))})")
print(f"new-build duplicate content keys (collisions): {dup}")
print(f"CARRIED over: {len(carried)}  ({dict(Counter(r['verdict'] for r,_,_ in carried))})")
print(f"DROPPED (window changed/gone): {len(dropped)}  ({dict(Counter(r['verdict'] for r,_ in dropped))})")
print(f"UNFOUND in old build (should be 0): {len(unfound)}")
print(f"\nWrote {OUT} with {len(out_records)} remapped verdicts")
print("\n=== sample DROPPED (codepoints : verdict : old-new-render reason) ===")
for rec, old in dropped[:12]:
    a=(old.get('after') or {}).get('cells')
    print(f"  {old.get('codepoints'):<24} {rec['verdict']:<8} {old.get('notation','')}")
