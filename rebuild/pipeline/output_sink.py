"""Where the table build's plain memo files go. A stamped `build-tables` writes each configuration's finished trace memo as `memo-<config>.tsv` beside its tables, which `run_m1.build_tables` packs for the next build's `run_m1.previous_memos` to read, unless `--output-sink=null` sends the files to a null sink: the crate then makes the same bytes, with every check its writer makes, and writes none of them (`OutputSink` in `rebuild/kernel-rs/src/memo.rs`). The tables and digests are the same either way.

`kernel_exec.build_table_files` passes the flag only when the fault kit runs the build, which the kit marks by setting `cycle_timings.CYCLE_RUN_ENV` to `FAULT_KIT_RUN` in every step's environment (`fault_kit.step_env`). Nothing the kit checks reads a memo file: their one reader is `run_m1.previous_memos`, which only a table build calls, so under the null sink every kit build runs cold, and neither `run_m1 --conform-only` nor `deep_replay` reaches it (`rebuild/test_output_sink.py`).
"""

from __future__ import annotations

import os

# `cycle_timings.CYCLE_RUN_ENV`, spelled out because importing that module would pull its imports into the table build's code closure (rebuild/test_build_code_closure.py). rebuild/test_output_sink.py builds under the kit's own environment, so the two cannot drift apart unnoticed.
CYCLE_RUN_ENV = "AMS_CYCLE_RUN"
FAULT_KIT_RUN = "fault-kit"
NULL_SINK_FLAG = "--output-sink=null"


def memo_sink_flags() -> tuple[str, ...]:
    """The flags that tell a stamped `build-tables` where its memo files go: `--output-sink=null` when the fault kit runs this process, read at call time, and none otherwise, so every other build writes its memo files."""
    return (NULL_SINK_FLAG,) if os.environ.get(CYCLE_RUN_ENV) == FAULT_KIT_RUN else ()
