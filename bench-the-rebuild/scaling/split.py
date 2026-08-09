"""Where does build_tables' time go: the settlement fixpoint vs. the table-compression tail?
Measured on a rune subset so it costs seconds, not minutes. Ratios are the object."""

import time, os, sys, json
from dataclasses import replace

t0 = time.process_time()
w0 = time.perf_counter()
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline import table as T, settle as S

imp_cpu = time.process_time() - t0
imp_w = time.perf_counter() - w0

t0 = time.process_time()
w0 = time.perf_counter()
spec = load_default_spec()
spec_cpu = time.process_time() - t0
spec_w = time.perf_counter() - w0
print(f"import            {imp_cpu:7.3f} CPU-s  {imp_w:7.3f} wall")
print(f"load_default_spec {spec_cpu:7.3f} CPU-s  {spec_w:7.3f} wall   runes={len(spec.runes)}")

KEEP = int(os.environ.get("KEEP", "8"))
names = sorted(spec.runes)
ligs = [n for n in names if spec.runes[n].sequence]
keep = set()
for n in ligs:
    keep.add(n)
    keep.update(spec.runes[n].sequence)
for n in names:
    if len(keep) >= KEEP:
        break
    keep.add(n)
sub = replace(spec, runes={n: r for n, r in spec.runes.items() if n in keep})
print(
    f"subset runes={len(sub.runes)} (ligatures kept: {sorted(n for n in sub.runes if sub.runes[n].sequence)})"
)

# instrument the tail
orig_rules_for_input = T._rules_for_input
orig_flag = T._flag_prospect_joints
acc = {"rules": 0.0, "flag": 0.0}


def timed_rules(*a, **k):
    t = time.perf_counter()
    r = orig_rules_for_input(*a, **k)
    acc["rules"] += time.perf_counter() - t
    return r


def timed_flag(*a, **k):
    t = time.perf_counter()
    r = orig_flag(*a, **k)
    acc["flag"] += time.perf_counter() - t
    return r


T._rules_for_input = timed_rules
T._flag_prospect_joints = timed_flag

t = time.perf_counter()
c = time.process_time()
decision, treaty = T.build_tables(sub, frozenset())
total = time.perf_counter() - t
totc = time.process_time() - c
T._rules_for_input = orig_rules_for_input
T._flag_prospect_joints = orig_flag

t = time.perf_counter()
decision.assert_outcome_partition()
part = time.perf_counter() - t
t = time.perf_counter()
decision.assert_e_stranded()
estr = time.perf_counter() - t

fix = total - acc["rules"] - acc["flag"]
print()
print(f"build_tables total     {total:8.3f} wall  ({totc:.3f} CPU)")
print(f"  fixpoint+liveness    {fix:8.3f}  {100*fix/total:5.1f}%")
print(f"  _flag_prospect_joints{acc['flag']:8.3f}  {100*acc['flag']/total:5.1f}%")
print(f"  _rules_for_input     {acc['rules']:8.3f}  {100*acc['rules']/total:5.1f}%")
print(f"post: assert_outcome_partition {part:8.3f} ({100*part/total:5.1f}% of build)")
print(f"post: assert_e_stranded        {estr:8.3f} ({100*estr/total:5.1f}% of build)")
print(
    f"windows={len(decision.transitions)} rules={len(decision.rules)} cells={len(decision.reachable_cells())} treaty={len(treaty.rows)}"
)
