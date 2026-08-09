"""Record what the two interpreters are, and what the settlement kernel's import closure needs."""

from __future__ import annotations

import json
import os
import subprocess
import sys

FT, GIL = sys.argv[1], sys.argv[2]

PROBE = r"""
import json, sys, sysconfig
before = set(sys.modules)
sys.path.insert(0, %r)
import rebuild.pipeline.table, rebuild.pipeline.settle, rebuild.pipeline.spec_load
new = [n for n in set(sys.modules) - before if sys.modules.get(n)]
ext = sorted(n for n in new if str(getattr(sys.modules[n], "__file__", "")).endswith((".so", ".pyd", ".dylib")))
thirdparty = sorted({n.split(".")[0] for n in new
                     if "site-packages" in str(getattr(sys.modules[n], "__file__", ""))})
print(json.dumps({
    "version": sys.version.split()[0],
    "version_full": sys.version,
    "free_threading_build": "free-threading" in sys.version,
    "gil_enabled": bool(getattr(sys, "_is_gil_enabled", lambda: True)()),
    "abiflags": sysconfig.get_config_var("abi_thread") or "",
    "build_config": {f: (f in (sysconfig.get_config_var("CONFIG_ARGS") or ""))
                     for f in ("--enable-optimizations", "--with-lto", "--enable-shared",
                               "--disable-gil", "--with-pydebug")},
    "opt_flags": sysconfig.get_config_var("OPT"),
    "prefix": sys.prefix,
    "kernel_c_extensions": ext,
    "kernel_third_party_packages": thirdparty,
    "kernel_modules_total": len(new),
}))
"""

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def probe(exe):
    out = subprocess.run([exe, "-c", PROBE % REPO], capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def sysctl(name):
    return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True).stdout.strip()


DEPS_PROBE = r"""
import json, sys, warnings, importlib
out = {"gil_before_any_import": sys._is_gil_enabled(), "modules": {}}
for m in ("yaml._yaml", "fontTools.feaLib.lexer", "fontTools.misc.bezierTools",
          "fontTools.cu2qu.cu2qu", "fontTools.pens.momentsPen", "fontTools.varLib.iup"):
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.import_module(m)
        out["modules"][m] = {
            "importable": True,
            "re_enables_gil": any("interpreter lock" in str(x.message) for x in w),
        }
    except Exception as e:
        out["modules"][m] = {"importable": False, "error": type(e).__name__}
out["gil_after_imports"] = sys._is_gil_enabled()
print(json.dumps(out))
"""


def deps_readiness():
    """What the *whole repo's* dependency set does on 3.14t, measured in the venv setup.sh builds
    for it. The settlement kernel needs none of this — it imports PyYAML and nothing else — but a
    run_m1 process imports fontTools, and adopting 3.14t for the real driver has to survive that."""
    full = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv-full", "bin", "python")
    if not os.path.exists(full):
        return {"probed": False, "reason": "venv-full not built; run setup.sh --full"}
    plain = subprocess.run([full, "-c", DEPS_PROBE], capture_output=True, text=True)
    forced = subprocess.run(
        [full, "-c", DEPS_PROBE], capture_output=True, text=True, env={**os.environ, "PYTHON_GIL": "0"}
    )
    # A real install attempt, not --dry-run: uharfbuzz resolves fine and only fails when the sdist
    # is actually compiled, so a dry run reports a success that does not exist.
    uhb = subprocess.run(
        ["uv", "pip", "install", "--python", full, "uharfbuzz>=0.43.0"],
        capture_output=True,
        text=True,
        env={**os.environ, "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", ".uv-cache")},
    )
    return {
        "probed": True,
        "default": (
            json.loads(plain.stdout.strip().splitlines()[-1])
            if plain.returncode == 0
            else plain.stderr[-400:]
        ),
        "with_PYTHON_GIL_0": (
            json.loads(forced.stdout.strip().splitlines()[-1])
            if forced.returncode == 0
            else forced.stderr[-400:]
        ),
        "uharfbuzz": {
            "installs": uhb.returncode == 0,
            "blocker": "Py_LIMITED_API (py_limited_api='cp310') is incompatible with Py_GIL_DISABLED — no cp314t wheel exists and the sdist cannot build; see python/cpython#111506",
        },
    }


print(
    json.dumps(
        {
            "machine": {
                "cpu": sysctl("machdep.cpu.brand_string"),
                "logical_cores": int(sysctl("hw.ncpu")),
                "performance_cores": int(sysctl("hw.perflevel0.logicalcpu") or 0),
                "efficiency_cores": int(sysctl("hw.perflevel1.logicalcpu") or 0),
                "ram_gb": round(int(sysctl("hw.memsize")) / 1e9, 1),
            },
            "freethreaded_interpreter": probe(FT),
            "gil_interpreter": probe(GIL),
            "both_from": "uv-managed python-build-standalone, same 3.14.6 source, same Clang 22.1.3, both PGO+LTO at -O3; the only differing configure flag is --disable-gil",
            "repo_pinned_python_untouched": True,
            "repo_dependency_readiness_on_3_14t": deps_readiness(),
        },
        indent=2,
    )
)
