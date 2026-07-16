# Rebuild tools

Long-running scripts for the M1 rebuild that the reports under `rebuild/` and `WHATNEXT.md` cite. All run from the repo root (some import `rebuild.pipeline`, so use `PYTHONPATH=. uv run python rebuild/tools/<script>.py` where the docstring says so).

| Script                                     | Purpose                                                                                                                                      |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `carry_verdicts.py`                        | Re-resolves prior verdict exports against the surfaces they were recorded on and carries render-identical units onto the live review surface |
| `scratch_build.py`                         | Parameterized `load_spec(runes_dir, ...)` build into a scratch out-dir, for A/B oracle experiments without touching `glyph_data/runes/`      |
| `probe.py`                                 | Probe one codepoint window: old-font baseline vs new settlement, all configs                                                                 |
| `seam_loss_probe.py` / `seam_loss_diff.py` | Group-scoped probes for the seam-loss-withdrawal triage                                                                                      |
| `t7_audit_diff.py`                         | A/B diff of two scratch-build divergence audits (the Group C verification)                                                                   |
| `jitter_audit.py`                          | Flags likely-misclick minority verdicts inside otherwise-unanimous families                                                                  |
| `review_docket.py`                         | Clusters blank human units across echo groups (the echo key minus the judged pair) and renders docket.html for class-grain adjudication      |
| `auto_classify_ss10.py`                    | The ss10 no-ligature auto-approval pass (2026-06-28)                                                                                         |
| `remap_verdicts.py`                        | Content-keyed verdict remap across a surface rebuild (the pulled-back removal)                                                               |
| `eyeball_triage.py`                        | Fixed-seed sample re-shaping check from the baseline report                                                                                  |
| `build_round1_assertions.py`               | The round-1 acceptance-assertion inventory (reconnaissance task B)                                                                           |
| `round1_assertions_check.py`               | The round-1 Phase-3 assertion-bucket gate                                                                                                    |
