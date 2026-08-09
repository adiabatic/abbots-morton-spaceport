"""Levers 1+2 on the real font build: CSafeLoader and gc.freeze()/gc.disable().

Runs `tools/build_font.py glyph_data/ <tmp out dir>` in process — the same work
`make all` does, minus the 0.09 s typst step — and hashes the six OTFs so a lever
that moves a byte is caught. Writes only under bench-the-rebuild/levers/.

  lever_fontbuild.py --yaml shipped|cloader --gc on|off --out DIR
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import io
import json
import os
import resource
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def cpu_now() -> float:
    total = 0.0
    for who in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN):
        r = resource.getrusage(who)
        total += r.ru_utime + r.ru_stime
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", default="shipped", choices=("shipped", "cloader"))
    ap.add_argument("--gc", default="on", choices=("on", "off"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tools"))
    import yaml

    if args.yaml == "cloader":
        loader = yaml.CSafeLoader
        yaml.safe_load = lambda stream: yaml.load(stream, loader)
        yaml.safe_load_all = lambda stream: yaml.load_all(stream, loader)
        yaml.SafeLoader = loader

    t_import = time.perf_counter()
    import build_font

    import_wall = time.perf_counter() - t_import

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Departure Mono is copied into site/ by `make all`; build_font reads it from reference/.
    argv = ["build_font.py", str(ROOT / "glyph_data") + "/", str(out_dir) + "/"]

    if args.gc == "off":
        gc.collect()
        gc.freeze()
        gc.disable()

    gc_before = sum(s["collections"] for s in gc.get_stats())
    saved_argv, sys.argv = sys.argv, argv
    buf = io.StringIO()
    t0 = time.perf_counter()
    c0 = cpu_now()
    try:
        with redirect_stdout(buf):
            build_font.main()
    finally:
        sys.argv = saved_argv
    wall = time.perf_counter() - t0
    cpu = cpu_now() - c0
    gc_after = sum(s["collections"] for s in gc.get_stats())

    digests = {}
    for name in sorted(os.listdir(out_dir)):
        if name.endswith(".otf") or name.endswith(".fea"):
            digests[name] = hashlib.sha256((out_dir / name).read_bytes()).hexdigest()
    combined = hashlib.sha256("".join(f"{k}:{v}" for k, v in sorted(digests.items())).encode()).hexdigest()

    print(
        json.dumps(
            {
                "yaml": args.yaml,
                "gc": args.gc,
                "import_wall": round(import_wall, 4),
                "wall": round(wall, 4),
                "cpu": round(cpu, 4),
                "collections": gc_after - gc_before,
                "artifacts": len(digests),
                "artifact_digest": combined,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
