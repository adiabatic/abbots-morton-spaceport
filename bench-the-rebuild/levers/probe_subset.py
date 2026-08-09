"""Probe: how does build_tables cost scale with the rune subset? Read-only, writes nothing."""

import sys, time, dataclasses, hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from rebuild.pipeline import conform
from rebuild.pipeline import table as table_module
from rebuild.pipeline.spec_load import load_default_spec

spec = load_default_spec()
features = conform.features_for_config("default")


def subset(names):
    return dataclasses.replace(spec, runes={k: v for k, v in spec.runes.items() if k in names})


def digest(decision, treaty):
    h = hashlib.sha256()
    h.update(
        repr(sorted(decision.transitions.items()) if hasattr(decision, "transitions") else decision).encode()
    )
    return h.hexdigest()[:16]


sets = {
    "s6": ["qsAh", "qsDay", "qsIt", "qsMay", "qsPea", "qsUtter"],
    "s8": ["qsAh", "qsDay", "qsIt", "qsMay", "qsPea", "qsUtter", "qsSee", "qsTea"],
    "s10": ["qsAh", "qsDay", "qsIt", "qsMay", "qsPea", "qsUtter", "qsSee", "qsTea", "qsOy", "qsRoe"],
    "s10L": [
        "qsAh",
        "qsDay",
        "qsIt",
        "qsMay",
        "qsPea",
        "qsUtter",
        "qsSee",
        "qsTea",
        "qsDay_qsUtter",
        "qsSee_qsUtter",
    ],
}
for label, names in sets.items():
    sp = subset(names)
    t0 = time.perf_counter()
    try:
        decision, treaty = table_module.build_tables(sp, features, trace_store=None, share=None)
    except Exception as exc:
        print(label, "FAILED", type(exc).__name__, str(exc)[:200], flush=True)
        continue
    dt = time.perf_counter() - t0
    print(
        f"{label:5s} n={len(names):2d} wall={dt:7.3f}s windows={len(decision.rows) if hasattr(decision,'rows') else '?'}",
        flush=True,
    )
    print("   attrs:", [a for a in dir(decision) if not a.startswith("_")][:12], flush=True)
