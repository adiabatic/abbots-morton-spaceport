"""How often the fixpoint touches the two module-level LRU caches that config-parallel threads would
share. Call counts are deterministic, so this is valid under contention."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kernel  # noqa: E402
from rebuild.pipeline import settle as settle_module  # noqa: E402
from rebuild.pipeline import table as table_module  # noqa: E402

KEEP = int(sys.argv[1]) if len(sys.argv) > 1 else 9
counts = {"guard_state": 0, "formation_blocked": 0, "liveness_probe": 0}

for name, mod in (("guard_state", settle_module), ("formation_blocked", settle_module), ("liveness_probe", table_module)):
    orig = getattr(mod, "_" + name if name != "formation_blocked" else name)

    def wrap(orig=orig, name=name):
        def f(*a, **k):
            counts[name] += 1
            return orig(*a, **k)

        return f

    setattr(mod, "_" + name if name != "formation_blocked" else name, wrap())

spec = kernel.load(KEEP)
for c in kernel.CONFIGS:
    kernel.build_one(spec, c)
print(json.dumps({"keep": KEEP, "runes": len(spec.runes), "configs": len(kernel.CONFIGS), **counts}))
