"""How hard is the spec handoff? Mechanically serialize ResolvedSpec to JSON and measure."""
import dataclasses, json, time, sys
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline import model as M

def enc(o):
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {"__t": type(o).__name__, **{f.name: enc(getattr(o, f.name)) for f in dataclasses.fields(o)}}
    if isinstance(o, dict): return {str(k): enc(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [enc(v) for v in o]
    if isinstance(o, (frozenset, set)): return sorted(enc(v) for v in o)
    return o

t=time.perf_counter(); spec = load_default_spec(); load=time.perf_counter()-t
t=time.perf_counter(); blob = json.dumps(enc(spec), sort_keys=True); dump=time.perf_counter()-t
print(f"load_default_spec {load:.3f}s   serialize {dump:.3f}s   JSON {len(blob)/1024:.1f} KiB")
# distinct dataclass types that appear
seen={}
def walk(o):
    if dataclasses.is_dataclass(o) and not isinstance(o,type):
        seen[type(o).__name__]=seen.get(type(o).__name__,0)+1
        for f in dataclasses.fields(o): walk(getattr(o,f.name))
    elif isinstance(o,dict):
        for v in o.values(): walk(v)
    elif isinstance(o,(list,tuple,set,frozenset)):
        for v in o: walk(v)
walk(spec)
print("dataclass instances in the resolved spec:")
for k,v in sorted(seen.items(), key=lambda r:-r[1]): print(f"   {v:6d}  {k}")
print("distinct types:", len(seen))
# how many predicate classes / heights / features
print("heights:", dict(spec.registry.heights))
print("features:", sorted(spec.registry.features))
print("predicate classes:", len(spec.registry.predicate_classes), "families:", len(spec.registry.families))
print("runes:", len(spec.runes), "stances:", sum(len(r.stances) for r in spec.runes.values()))
pol = {k: sum(len(getattr(r.policy,k)) for r in spec.runes.values()) for k in ("refuse","prefer","extend","contract","resolve")}
print("policy records:", pol, "total", sum(pol.values()))
open("bench-the-rebuild/scaling/spec.json","w").write(blob)
