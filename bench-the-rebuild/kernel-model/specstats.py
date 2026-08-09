import json, sys
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline import table as T
spec = load_default_spec()
out = {}
out["n_runes"] = len(spec.runes)
out["heights"] = dict(spec.registry.heights)
out["features"] = {k: v.kind for k, v in spec.registry.features.items()}
out["predicate_classes"] = {k: len(v) for k, v in spec.registry.predicate_classes.items()}
runes = []
for name, r in sorted(spec.runes.items()):
    rec = {
        "name": name,
        "seq": r.sequence,
        "n_stances": len(r.stances),
        "order": list(r.policy.order),
        "n_refuse": len(r.policy.refuse),
        "n_prefer": len(r.policy.prefer),
        "n_extend": len(r.policy.extend),
        "n_contract": len(r.policy.contract),
        "n_resolve": len(r.policy.resolve),
        "groups": {k: len(v) for k, v in r.policy.groups.items()},
        "stances": [],
    }
    for sname, st in r.stances.items():
        rec["stances"].append({
            "name": sname,
            "traits": list(st.traits),
            "entries": {h: {"selectable": row.selectable, "n_scope": len(row.scope)} for h, row in st.surface.entries.items()},
            "exits": {h: {"n_scope": len(row.scope)} for h, row in st.surface.exits.items()},
            "n_never": len(st.surface.pairings.never),
            "only": None if st.surface.pairings.only is None else len(st.surface.pairings.only),
            "n_unlocks": len(st.surface.unlocks),
            "require": list(st.surface.require),
        })
    runes.append(rec)
out["runes"] = runes
out["depth3_inputs"] = sorted(T.depth3_inputs(spec))
out["depth4_inputs"] = sorted(T.depth4_inputs(spec))
def reach_hist(kind):
    h = {}
    for n, r in spec.runes.items():
        for rec2 in r.policy.prefer:
            if rec2.when.right is not None:
                k = T.right_chain_reach(rec2.when.right)
                h[k] = h.get(k, 0) + 1
    return h
out["prefer_right_reach_hist"] = reach_hist("prefer")
print(json.dumps(out, indent=1))
