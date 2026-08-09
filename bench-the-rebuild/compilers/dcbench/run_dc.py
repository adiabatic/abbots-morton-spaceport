import json, sys, time
import dckernel

N = 2_000_000
out = {"label": sys.argv[1], "native_module": dckernel.__file__.endswith(".so"), "n": N,
       "dataclass_dunder_is_interpreted": getattr(dckernel.Frozen.__dict__["__init__"], "__code__", None) is not None
       and dckernel.Frozen.__dict__["__init__"].__code__.co_filename == "<string>"}
for name in ("frozen_memo", "handwritten_memo", "tuple_memo"):
    fn = getattr(dckernel, name)
    fn(20_000)
    best, checks = None, []
    for _ in range(3):
        t0 = time.perf_counter(); checksum = fn(N); dt = time.perf_counter() - t0
        checks.append(checksum)
        best = dt if best is None else min(best, dt)
    out[name] = {"ns_per_key": round(best / N * 1e9, 2), "checksum": checks[0],
                 "checksums_agree": len(set(checks)) == 1}
print(json.dumps(out))
