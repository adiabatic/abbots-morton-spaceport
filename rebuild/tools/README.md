# Rebuild tools

Long-running scripts for the M1 rebuild. All run from the repo root (some import `rebuild.pipeline`, so use `PYTHONPATH=. uv run python rebuild/tools/<script>.py` where the docstring says so).

| Script              | Purpose                                                                                                                                                                 |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `artifact_cycle.py` | The mechanized commit-time artifact cycle (`make artifact-cycle`): snapshot the served surface, `run_m1`, in-place surface rebuild, verdict carry, census re-pin, gates |
| `carry_verdicts.py` | Re-resolves prior verdict exports against the surfaces they were recorded on and carries render-identical units onto the live review surface                            |
| `echo_verdicts.py`  | Emits fill records for blank units in unanimously-verdicted echo groups and prints every group whose recorded verdicts disagree, each with a `#units=` deep-link        |
| `review_docket.py`  | Clusters blank human units across echo groups (the echo key minus the judged pair) and renders docket.html for class-grain adjudication                                 |
| `scratch_build.py`  | Parameterized `load_spec(runes_dir, ...)` build into a scratch out-dir, for A/B oracle experiments without touching `glyph_data/runes/`                                 |
| `probe.py`          | Probe one codepoint window: old-font baseline vs new settlement, all configs                                                                                            |
