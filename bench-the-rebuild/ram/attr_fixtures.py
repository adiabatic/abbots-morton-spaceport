"""Attribution run (c) of issue #51: what `rebuild/conftest.py`'s session fixtures materialize per xdist worker, built by the very loader functions the fixtures call so the figures cannot drift from what a worker actually pays. Phase one is the un-enriched workload (the `workload` fixture, whose docstring's ≈0.8 GB the tracker measured 2.4× stale — sub-issue #58); phase two is the enriched universe (`enriched_units`), measured as growth on top of phase one. That second phase loads its own workload internally, exactly as `_enrich_workload` does for the fixture, and a real worker holding both session fixtures holds both graphs — so the end state here is the honest per-worker shape, not a worst case.

AMS_ATTR_TRACE=0 runs it untraced for the clean high-water figure.

Run as: uv run python bench-the-rebuild/ram/attr_fixtures.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

import attr_common


def main() -> None:
    from rebuild import conftest as fixtures

    attr_common.start()
    workload = fixtures._load_live_workload()
    load_record, load_snapshot = attr_common.phase("load_workload")
    attr_common.print_summary(load_record)
    units = fixtures._enrich_workload()
    enrich_record, _ = attr_common.phase("enrich_workload", since=load_snapshot)
    attr_common.print_summary(enrich_record)
    row = {
        "workload_units": len(workload.units),
        "enriched_units": len(units),
        "phases": [load_record, enrich_record],
    }
    path = attr_common.write_result("attr-fixtures" + ("" if attr_common.TRACING else "-untraced"), row)
    print(f"[attr] wrote {path}", flush=True)


if __name__ == "__main__":
    main()
