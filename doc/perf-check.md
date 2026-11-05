# Speed and output readings for a change

A change meant to shorten edit-to-READY answers #584's three questions: whether it is faster, whether its outputs are the same, and whether the gates still catch what they caught. #618's decision 47 sets how each is read here: no paired timing, and nothing written but a fingerprint and the fault kit's small-alphabet builds.

## Speed: the live journal

Every cycle pass appends its run line and step lines to `rebuild/out/cycle-timings.ndjson`. Read `make cycle-timings ARGS='--by-step'` and the phase lines on matched pass shapes before and after the change's commit, on the machine it lands on, and name each side's n. The passes are not paired and their edits differ, so a difference inside the spread of that shape's passes is reported as not separated, never as a speed-up or a slowdown. A change need not be faster; it reports what the journal shows and any trade by name.

## Same output: the fingerprint

`rebuild/tools/perf_fingerprint.py` reads every configuration's table digest, the compiled and site fonts, the review corpus without its stamps, the corpus stores, the verdict store, the complaint list and the review-facts pins; its module docstring is the authority on what each part covers and leaves out. Take one reading of the live tree before the change's first build and one after its landing pass:

```sh
uv run python -P rebuild/tools/perf_fingerprint.py --root . --out var/keep/perf/<change>-before.json
```

The two readings are equal but for the differences the change names. A verdict made between them shows as a `verdicts` difference. The fingerprint leaves memo files out, so a change that touches the settle memos also compares each configuration's memo sha256 while memo files exist.

## The fault kit

A run refuses with exit 2 (`fault_kit.WITHDRAWN`) until #618's change 0k runs its rows on small alphabets, since each row builds the whole alphabet today; `--list` still runs.

`rebuild/perf/faults.yaml` is the authority on the kit's rows: each row's patch, the gates that catch it, and the steps that reach them at the current head. `rebuild/tools/fault_kit.py` runs it in a scratch clone under `var/perf/`, and its module docstring is the authority on a run: `--rows auto` selects every row whose catching gates (their files, their modules' import closure, and the crate when a gate rests on it) the paths in `--changed` touch, `--rows all` selects every row, and `--base` runs the rows not caught again on the base commit.

| Result        | Meaning                                                                                                |
| ------------- | ------------------------------------------------------------------------------------------------------ |
| `caught`      | Every step met its expected exit and its required text                                                 |
| `inert`       | The fault did nothing on this tree (exit 0, effect files unchanged)                                    |
| `missed`      | The fault took effect and its gate let it through                                                      |
| `wrong-catch` | Another exit, a timeout, or a missing required text                                                    |
| `stale-patch` | The row's patch does not apply                                                                         |
| `lost`        | The base catches it and the candidate does not: the candidate lost or changed the catch                |
| `re-author`   | The base catches it and the candidate moved the injection site: re-author the patch in the same change |
| `drifted`     | The base does not catch it either: the row no longer fits the head, whatever the candidate did         |

The first step that misses its expectation decides a row's result, since a later step can run on outputs an earlier one never rewrote. To re-author a row, apply the old patch's intent by hand in a clone of the tree it should fit, then write `git diff` over the row's patch file.

Row 13 reaches the fold's certificate check, through the unsupported-right-slot refusal alone, and row 03 stops in the enumeration before the fold runs. No row reaches the witness stage: row 01 stops at the string replay, which `run_m1` raises before the witness stage starts, and row 13 stops in the fold, which writes no table for the stage to read. So a change that touches the witness stage's code is reported `unreached: witness` beside the rows it runs, while a crate change counts the `fold` gate as reached whichever branch it touches; `doc/rebuild-design.md` §10 records both gaps.

## What stays under `var/keep/perf/`

The records of the whole-pass checks run before #618's decision 47, one directory a run, and the fingerprint readings taken for the bar.
