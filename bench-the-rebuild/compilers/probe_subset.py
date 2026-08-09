import time, sys, dataclasses, hashlib
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.table import build_tables

spec = load_default_spec()
subsets = {
    "s6": ["qsAh", "qsDay", "qsFee", "qsIt", "qsMay", "qsUtter"],
    "s8": ["qsAh", "qsDay", "qsFee", "qsIt", "qsMay", "qsUtter", "qsPea", "qsSee"],
    "s10": ["qsAh", "qsDay", "qsFee", "qsIt", "qsMay", "qsUtter", "qsPea", "qsSee", "qsRoe", "qsTea"],
    "s12": [
        "qsAh",
        "qsDay",
        "qsFee",
        "qsIt",
        "qsMay",
        "qsUtter",
        "qsPea",
        "qsSee",
        "qsRoe",
        "qsTea",
        "qsNo",
        "qsLow",
    ],
    "s13l": [
        "qsAh",
        "qsDay",
        "qsFee",
        "qsIt",
        "qsMay",
        "qsUtter",
        "qsPea",
        "qsSee",
        "qsRoe",
        "qsTea",
        "qsNo",
        "qsLow",
        "qsSee_qsUtter",
    ],
}
name = sys.argv[1]
keep = subsets[name]
sub = dataclasses.replace(spec, runes={k: v for k, v in spec.runes.items() if k in keep})
t0 = time.perf_counter()
dt_, tt_ = build_tables(sub, frozenset())
t1 = time.perf_counter()
h = hashlib.sha256()
for w in dt_.transitions:
    h.update(("\t".join(w.key) + "\t" + w.outcome + "\n").encode())
print(
    f"{name}: {t1-t0:.3f}s rules={len(dt_.rules)} windows={len(dt_.transitions)} cells={len(dt_.reachable_cells())} treaty={len(tt_.rows)} sha={h.hexdigest()[:16]}",
    file=sys.stderr,
)
