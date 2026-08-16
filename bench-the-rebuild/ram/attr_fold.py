"""Attribution run (a) of issue #51 — "Peak B", the shipping fold path that had never been measured: `kernel_io.read_transitions` over a packed kernel stream and `table.assemble_tables` on the product, exactly as `run_m1._fold_stream` runs them for each configuration of a rust-engine build. The kernel enumerates the one requested configuration first (cargo and the crate are prerequisites, as for any M1 build); the tracer starts only at the fold, so the kernel's own footprint never pollutes the attribution. Two phases, snapshotted separately — what the reader materializes, then what assembly adds on top — because sub-issue #55's levers live on different sides of that line.

GC is frozen and disabled unless AMS_SCALING_GC=on, matching `run_m1`'s entry. AMS_ATTR_TRACE=0 runs it untraced for the clean high-water figure.

Run as: uv run python bench-the-rebuild/ram/attr_fold.py [config]   (defaults to the first acceptance configuration)
"""

import gc
import gzip
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

import attr_common
from rebuild.pipeline import conform, kernel_exec, kernel_io
from rebuild.pipeline import table as T
from rebuild.pipeline.spec_load import load_default_spec


def main() -> None:
    config = sys.argv[1] if len(sys.argv) > 1 else conform.ACCEPTANCE_CONFIGS[0]
    spec = load_default_spec()
    kernel_exec.cargo_build()
    if os.environ.get("AMS_SCALING_GC", "off") != "on":
        gc.collect()
        gc.freeze()
        gc.disable()
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        spec_path = directory / "spec.json"
        kernel_io.write_spec(spec, spec_path)
        start = time.perf_counter()
        streams = kernel_exec.enumerate_configs(
            spec_path, directory / "streams", (config,), threads=1, timings=True
        )
        print(f"[attr] kernel enumerate [{config}] {time.perf_counter() - start:.1f}s", flush=True)
        stream = streams[config]
        packed = directory / f"{stream.stem}.ndjson.gz"
        with (
            stream.open("rb") as plain,
            packed.open("wb") as raw,
            gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=1) as handle,
        ):
            shutil.copyfileobj(plain, handle)
        stream.unlink()
        attr_common.start()
        start = time.perf_counter()
        product = kernel_io.read_transitions(packed)
        read_wall = time.perf_counter() - start
        read_record, read_snapshot = attr_common.phase("read_transitions")
        attr_common.print_summary(read_record)
        start = time.perf_counter()
        decision, treaty = T.assemble_tables(spec, product)
        assemble_wall = time.perf_counter() - start
        assemble_record, _ = attr_common.phase("assemble_tables", since=read_snapshot)
        attr_common.print_summary(assemble_record)
    row = {
        "config": config,
        "windows": len(decision.transitions),
        "rules": len(decision.rules),
        "treaty_rows": len(treaty.rows),
        "read_wall_s": round(read_wall, 1),
        "assemble_wall_s": round(assemble_wall, 1),
        "gc": "on" if gc.isenabled() else "frozen",
        "phases": [read_record, assemble_record],
    }
    path = attr_common.write_result(f"attr-fold-{config}" + ("" if attr_common.TRACING else "-untraced"), row)
    print(f"[attr] wrote {path}", flush=True)


if __name__ == "__main__":
    main()
