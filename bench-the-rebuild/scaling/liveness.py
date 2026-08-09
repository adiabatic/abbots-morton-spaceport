"""How much of a cold fixpoint is the deep-slot liveness probing (_ProspectLiveness),
the part WHATNEXT names as the residual floor of a fully-served (warm) build and the
subtlest 336 lines a port would have to reproduce exactly?"""

import time, os
from dataclasses import replace
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline import table as T

spec = load_default_spec()
KEEP = int(os.environ.get("KEEP", "12"))
names = sorted(spec.runes)
base = []
for n in [x for x in names if spec.runes[x].sequence]:
    for p in spec.runes[n].sequence:
        if p not in base:
            base.append(p)
    base.append(n)
for n in names:
    if n not in base:
        base.append(n)
keep = set(base[:KEEP])
keep = {n for n in keep if not spec.runes[n].sequence or set(spec.runes[n].sequence) <= keep}
sub = replace(spec, runes={n: r for n, r in spec.runes.items() if n in keep})

acc = {"third_live": [0, 0.0], "fourth_live": [0, 0.0]}
depth = [0]
for meth in ("third_live", "fourth_live"):
    orig = getattr(T._ProspectLiveness, meth)

    def make(orig=orig, meth=meth):
        def wrapper(self, *a, **k):
            acc[meth][0] += 1
            if depth[0] == 0:
                depth[0] = 1
                t = time.perf_counter()
                try:
                    return orig(self, *a, **k)
                finally:
                    acc[meth][1] += time.perf_counter() - t
                    depth[0] = 0
            return orig(self, *a, **k)

        return wrapper

    setattr(T._ProspectLiveness, meth, make())

t = time.perf_counter()
d, tr = T.build_tables(sub, frozenset())
total = time.perf_counter() - t
liv = acc["third_live"][1] + acc["fourth_live"][1]
print(f"runes={len(sub.runes)} windows={len(d.transitions)}")
print(f"build_tables total          {total:8.3f}s")
print(f"  liveness probes (top-level){liv:8.3f}s  {100*liv/total:5.1f}%")
for m, (n, s) in acc.items():
    print(f"    {m:12s} calls={n:8d}  outermost {s:7.3f}s")
print(f"  everything else            {total-liv:8.3f}s  {100*(total-liv)/total:5.1f}%")
