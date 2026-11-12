# Speed and output readings for a change

A change meant to shorten edit-to-READY answers #584's three questions: whether it is faster, whether its outputs are the same, and whether the gates still catch what they caught. #618's decision 47 sets how each is read here: no paired timing, and nothing written but a fingerprint and the fault kit's small-alphabet builds.

## Speed: the live journal

Every cycle pass appends its run line and step lines to `rebuild/out/cycle-timings.ndjson`. Read `make cycle-timings ARGS='--by-step'` and the phase lines on matched pass shapes before and after the change's commit, on the machine it lands on, and name each side's n. The passes are not paired and their edits differ, so a difference inside the spread of that shape's passes is reported as not separated, never as a speed-up or a slowdown. A change need not be faster; it reports what the journal shows and any trade by name.

## Same output: the fingerprint

`rebuild/tools/perf_fingerprint.py` reads every configuration's table digest, the compiled and site fonts, the review corpus without its stamps, the corpus stores, the verdict store, the complaint list and the review-facts pins; its module docstring is the authority on what each part covers and leaves out. An output held as a base and segments is read in its materialized form (#618's decision 48), the file a whole write gives, so a corpus a build appended to, folded or holds deflated fragments in, read with every fragment inflated (change 35), its unit store's segments and each configuration's windows rows read as a from-scratch build's files, and a later fold or append moves no reading. A configuration's table digest is recomputed from its materialized rows, hashed a row at a time as they stream, every part checked against its head, and the reading exits nonzero naming the configuration when the digest is not the crate's own that its windows head records. The verdict store's projection is hashed as the store streams a record at a time, for the value a whole parse gives. Take one reading of the live tree before the change's first build and one after its landing pass:

```sh
uv run python -P rebuild/tools/perf_fingerprint.py --root . --out var/keep/perf/<change>-before.json
```

The two readings are equal but for the differences the change names. A verdict made between them shows as a `verdicts` difference.

## The fault kit

`rebuild/perf/faults.yaml` is the authority on the kit's rows: each row's patch, its alphabet, the gates that catch it, and the steps that reach them at the current head. `rebuild/tools/fault_kit.py` runs them, and its module docstring is the authority on a run: `--rows auto` selects every row whose catching gates (their files, their modules' import closure, and the crate when a gate rests on it) the paths in `--changed` touch, `--rows all` selects every row, and `--base` runs the rows not caught again on the base commit.

Each row builds only its own small alphabet, the runes its `alphabet:` names, through the alphabet switch (`AMS_M1_RUNES`, `rebuild/pipeline/spec_load.py`). Only the kit sets it: the cycle, `run_m1`, the subset refilter (`rebuild/pipeline/baseline_subset.py`), the review build, the verdict update and the land refuse to run under it otherwise, so a shell that leaks it cannot build a small alphabet into the live tree. A row's alphabet is the smallest that keeps its rule shape, so that the fault still takes effect, and passes the unfaulted build, the control, which asks for three things of any alphabet. It holds a Manual pin in scope, since `run_m1` fails a build with none (#618's decision 59). It holds a ligature, since without one the formation lookup comes out empty and read-back fails the build. And it joins at each of the four cursive heights, since the Manual-pin gate's junction classifier (`rebuild/validation/classify.py`) refuses a font with fewer; the manifest's alphabets show letters that do. `rebuild/test_fault_kit.py` checks the pin, the ligature and the switch's rules for every row, and the control checks the rest. Choose one by running the kit on the row: a row `inert` on its alphabet needs letters the alphabet lacks, and so can one `missed` there, when the fault takes effect but no text its gate checks on that alphabet reaches it. Rows share an alphabet where their letters allow: a row whose smallest alphabet lies inside another row's takes that one, since each alphabet costs a control build whenever the sources move and the extra runes add far less than that to the row's own builds.

The kit works in a tree of its own, `var/perf/kit/`, which it refreshes from this checkout's work tree at the start of every run, cloning with `cp -c` only the files that moved. Uncommitted changes are part of the tree under test, so `--base HEAD` is the base of an uncommitted change. A base without the alphabet switch, a commit from before #618's change 0k, is not run, since its builds would cover the whole alphabet. It keeps one control build per alphabet, and one per alphabet for the base, in `var/perf/kit-controls/`, and rebuilds a control only when the sources move. For an uncommitted change:

```zsh
{ git diff --name-only HEAD; git ls-files --others --exclude-standard; } > tmp/changed.txt
make fault-kit ARGS='--rows auto --changed tmp/changed.txt --base HEAD --out var/perf/kit-runs/<change>.json --log-dir var/perf/kit-runs/<change>-logs'
```

`make fault-kit ARGS='…'` runs the kit as a one-shot launchd job's own process (`rebuild/tools/job_launch.py`), so its resource coalition holds only the kit and what it starts, and follows the job's log in `tmp/fault-kit.log`. The kit's meter gate refuses a run unless the write meter's first reading is in the own basis, and caps each run, its own invocation, at `fault_kit.WRITE_CAP_BYTES` of the coalition's logical writes: at `fault_kit.WRITE_WATERMARK_BYTES` it stops every other member of the coalition, records the stop, kills them, and exits `fault_kit.WRITE_CAP_STATUS`. The `--out` record's `writes` block holds what the run wrote, with swap beside it and never counted against the cap, since the swap volume's figure is machine-wide. The module docstring's "The meter gate" says how the gate reads and stops.

| Result        | Meaning                                                                                                                     |
| ------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `caught`      | Every step met its expected exit and its required text                                                                      |
| `inert`       | The fault did nothing on this tree (exit 0, effect files unchanged)                                                         |
| `missed`      | The fault took effect and its gate let it through                                                                           |
| `wrong-catch` | Another exit, a timeout, or a missing required text                                                                         |
| `stale-patch` | The row's patch does not apply                                                                                              |
| `lost`        | The base catches it and the candidate does not: the candidate lost or changed the catch                                     |
| `re-author`   | The base catches it and the candidate moved the injection site: re-author the patch in the same change                      |
| `not-covered` | Inert on both sides: the row's alphabet shows the fault on neither tree                                                     |
| `drifted`     | The base does not catch it either, and it is not inert on both: the row no longer fits the head, whatever the candidate did |

The first step that misses its expectation decides a row's result, since a later step can run on outputs an earlier one never rewrote. To re-author a row, apply the old patch's intent by hand in a clone of the tree it should fit, then write `git diff` over the row's patch file.

Row 13 reaches the fold's certificate check, through the unsupported-right-slot refusal alone, and row 03 stops in the enumeration before the fold runs. No row reaches the witness stage: row 01 stops at the string replay, which `run_m1` raises before the witness stage starts, and row 13 stops in the fold, which writes no table for the stage to read. So a change that touches the witness stage's code is reported `unreached: witness` beside the rows it runs, while a crate change counts the `fold` gate as reached whichever branch it touches; `doc/rebuild-design.md` §10 records both gaps. Row 14 is the one row whose patch reaches the memo code (`engine::shared_memo_entry`): its build runs at width 1 (`env AMS_KERNEL_THREADS=1` on its argv, since the kit passes only its listed variables through), so every delta reads `default`'s snapshot, and it stops in the enumeration's member cross-check before a table is written.

## What stays under `var/keep/perf/`

The records of the whole-pass checks run before #618's decision 47, one directory a run, and the fingerprint readings taken for the bar.
