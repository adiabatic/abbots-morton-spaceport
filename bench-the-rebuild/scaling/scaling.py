"""How does the six-configuration M1 fixpoint grow with the modeled alphabet?
The decision this bears on: 15 of ~44 Quikscript letters are modeled today. If the
workload grows steeply in rune count, a 20x port buys years; if not, it buys months."""

import time, json, sys
from dataclasses import replace
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline import table as T

spec = load_default_spec()
names = sorted(spec.runes)
ligs = [n for n in names if spec.runes[n].sequence]
# deterministic nested subsets: ligature closure first, then alphabetical
base = []
for n in ligs:
    for part in spec.runes[n].sequence:
        if part not in base:
            base.append(part)
    if n not in base:
        base.append(n)
for n in names:
    if n not in base:
        base.append(n)

out = []
for k in [int(x) for x in sys.argv[1:]] or [6, 8, 10, 12, 14, 16, 18]:
    keep = set(base[:k])
    # ligatures need their components present
    keep = {n for n in keep if not spec.runes[n].sequence or set(spec.runes[n].sequence) <= keep}
    sub = replace(spec, runes={n: r for n, r in spec.runes.items() if n in keep})
    letters = sum(1 for n in sub.runes if not sub.runes[n].sequence)
    t = time.process_time()
    w = time.perf_counter()
    d, tr = T.build_tables(sub, frozenset())
    cpu = time.process_time() - t
    wall = time.perf_counter() - w
    row = dict(
        runes=len(sub.runes),
        letters=letters,
        ligs=len(sub.runes) - letters,
        windows=len(d.transitions),
        rules=len(d.rules),
        cells=len(d.reachable_cells()),
        cpu=round(cpu, 3),
        wall=round(wall, 3),
    )
    out.append(row)
    print(json.dumps(row), flush=True)
json.dump(out, open("bench-the-rebuild/scaling/scaling.json", "w"), indent=1)
# fit exponents on consecutive pairs
import math

print("\nrunes_a->runes_b   window exponent   cpu exponent")
for a, b in zip(out, out[1:]):
    r = math.log(b["runes"] / a["runes"])
    print(
        f"{a['runes']:2d}->{b['runes']:2d}   windows {math.log(b['windows']/a['windows'])/r:5.2f}   cpu {math.log(b['cpu']/a['cpu'])/r:5.2f}"
    )
