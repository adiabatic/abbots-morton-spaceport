"""How does the M1 fixpoint grow with the modeled alphabet? Only part of the 44-letter target is modeled today, so the decision this bears on is whether a 20x port buys years or months: if the workload grows steeply in rune count, a constant factor is spent quickly.

Run it from anywhere; k values are positional and default to the full ladder up to the current alphabet. The top row should reproduce the production default-config table, which is the check that this measures the real kernel rather than a subset of it.

Cyclic GC is frozen and disabled by default because that is what `run_m1` does at its entry, and the sweep is meant to model that stage. `AMS_SCALING_GC=on` leaves the collector running, which is the state the pre-#35 sweeps were measured in. `AMS_SCALING_ROOT` points the kernel import at a comparison tree instead of this repo, which is how an arm at an older revision is measured; `AMS_DEEP_CLASSES=0` is the label-grain arm, and each row records which of the two it ran under.

`AMS_SCALING_DUMP=<dir>` turns each rung into a kernel-boundary sample as well as a timing: the rung's sub-spec and its default-config fixpoint product are written into that directory, and the row gains the canonical differential digest of the rung's tables. Its timed region calls the two seam halves directly and so leaves out the class-grain assertions a plain `build_tables` runs, which are proof rather than product — the times a dump run reports are therefore its own arm, not rows to fit an exponent against a default run's, and the `mode` field is what says which arm a row came from.
"""

import gc, os, resource, time, json, sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, os.environ.get("AMS_SCALING_ROOT") or str(HERE.parents[2]))

from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline import table as T

DUMP = os.environ.get("AMS_SCALING_DUMP")
if DUMP:
    from rebuild.pipeline import kernel_io as K

    dump_dir = Path(DUMP)
    dump_dir.mkdir(parents=True, exist_ok=True)

if os.environ.get("AMS_SCALING_GC", "off") != "on":
    gc.collect()
    gc.freeze()
    gc.disable()

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

ladder = sorted({*range(6, len(base), 2), len(base)})
out = []
for k in [int(x) for x in sys.argv[1:]] or ladder:
    keep = set(base[:k])
    # ligatures need their components present
    keep = {n for n in keep if not spec.runes[n].sequence or set(spec.runes[n].sequence) <= keep}
    sub = replace(spec, runes={n: r for n, r in spec.runes.items() if n in keep})
    letters = sum(1 for n in sub.runes if not sub.runes[n].sequence)
    t = time.process_time()
    w = time.perf_counter()
    if DUMP:
        product = T.enumerate_transitions(sub, frozenset())
        d, tr = T.assemble_tables(sub, product)
    else:
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
        rss_gb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**3, 2),
        gc="on" if gc.isenabled() else "frozen",
        deep_classes=getattr(T, "DEEP_CLASSES_DEFAULT", None),
    )
    if DUMP:
        K.write_spec(sub, dump_dir / f"spec-r{len(sub.runes)}.json")
        K.write_transitions(product, dump_dir / f"transitions-r{len(sub.runes)}.gz")
        row["digest"] = T.table_digest(d, tr)
        row["mode"] = "dump"
    out.append(row)
    print(json.dumps(row), flush=True)
json.dump(out, open(HERE.parent / "scaling.json", "w"), indent=1)
# fit exponents on consecutive pairs
import math

print("\nrunes_a->runes_b   window exponent   cpu exponent")
for a, b in zip(out, out[1:]):
    r = math.log(b["runes"] / a["runes"])
    print(
        f"{a['runes']:2d}->{b['runes']:2d}   windows {math.log(b['windows']/a['windows'])/r:5.2f}   cpu {math.log(b['cpu']/a['cpu'])/r:5.2f}"
    )
