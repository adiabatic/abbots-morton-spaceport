# Rust port of the M1 settlement kernel — plan

An open fork. This file holds the decision, the thresholds that settle it, and the design facts that came out of measuring it. It deliberately does not carry the measurements themselves: the harnesses beside it regenerate those, and a number written here would be stale the moment the first lever lands.

## The decision

Whether to reimplement `rebuild/pipeline/table.build_tables` and its settlement engine in Rust, as a standalone binary reading a serialized spec and writing the same windows file.

**It is not a seconds-per-day decision.** The port's incremental value over the keep-the-Python stack is real but modest. What settles it is alphabet headroom: the fixpoint grows as letters^4.0–4.5, and the target is all 44 codepoint-bearing letters.

**The user's rationale, recorded:** all 44 letters eventually. That is what makes the port a _when_ rather than an _if_ — and also what makes it insufficient on its own, because at 44 letters even a parallel port lands near the edge of tolerance, and only at the optimistic end of both the multiplier and the exponent bands.

## Sequence

1. **The Python levers first**, and not mainly for their own sake. They move the denominator every port measurement is a ratio against; benchmarking a port against today's shipped kernel overstates its value by roughly 4×. They are also what keeps the next few migration batches bearable. `levers/apply_m1_patches.py` holds them.
2. **Re-baseline.** One `make artifact-cycle` so `rebuild/out/cycle-timings.ndjson` exists on this machine, then re-run `kernel-model/run.sh` and `scaling/scaling.py`. Until that happens every daily figure rests on an assumed build cadence that this machine's own green records contradict.
3. **Then decide**, against the thresholds below.

## Thresholds that settle it

Check these rather than re-litigating the argument. Each is a re-run, not a judgement.

| Threshold                                                | How to check                                                      | What it means                                                                                   |
| -------------------------------------------------------- | ----------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Alphabet target passes ~21 letters                       | the migration itself                                              | Every keep-the-Python path is exhausted; only a port reaches further                            |
| Six-config cold wall passes ~600 s after the levers land | `make cycle-timings`                                              | The levers are spent                                                                            |
| Measured exponent rises above ~4.5                       | `scaling/scaling.py` after each migration batch                   | Even a parallel port stops reaching 44; work avoidance becomes mandatory regardless of language |
| Kernel-semantics commits fall below ~1/quarter           | `git log -- rebuild/pipeline/{settle,table,model,specificity}.py` | The duplication tax collapses and the port gets much cheaper                                    |
| `TraceShare` can be made value-keyed                     | see the open question below                                       | Parallelism and the share stop excluding each other; every parallel figure rises                |

The exponent check is the cheapest and the most decisive, and it is the one nobody was running. Re-run it after each migration batch.

## Design facts

These came out of measuring the port rather than guessing at it, and they are the reason the project is smaller than it looks.

- **The cut line is `build_tables`, not the existing subprocess.** `run_m1` looks like a boundary but also mints glyphs, runs defect gates, emits GSUB/GPOS, compiles the font with fontTools and runs three HarfBuzz gates. One level in, the repo's own byte-stable `write_windows`/`read_windows` round-trip plus a JSON dump of the resolved spec already carry most of the interface. **A binary that reads a spec file and writes a windows file — no FFI.**
- **PyO3 is dominated, not merely worse.** Its only advantage over a file boundary is saving a serialization measured at a small fraction of the build, and passing Python objects across the boundary rebuilds the very object graph the packing exists to delete.
- **`spec_load.py` is outside the runtime closure** and does not need porting.
- **`trace_memo.py` should be deleted rather than ported.** At port speed the persisted memo's whole value collapses to a few seconds, which is less than the gzip and JSON it costs.
- **`_rules_for_input` stays in Python.** It is the largest and hairiest function in `table.py` and its output _is_ the shipped GSUB rule ordering — and it is a fraction of a percent of runtime.
- **The risk concentrates in `_ProspectLiveness`.** Most of the runtime lives in one class that is simultaneously the hottest, the subtlest and the easiest to get silently wrong: an under-opened liveness verdict omits windows, and the font is then wrong in a way only the conformance sweep catches.
- **The verification story is unusually strong and is the best argument for attempting it at all.** `rebuild/test_rule_witnesses.py::test_the_stamped_table_is_what_a_fresh_fixpoint_builds` already diffs a serialized enumeration against a fresh in-process fixpoint, so a Rust-vs-Python differential harness is a small piece of work. On top of that, gate:conform still settles in Python, which turns it into a differential mediated by an independent shaper across every swept string.
- **The port does not retire `settle.py`.** Many non-test modules reference it — `conform.py` settles every swept text in Python, and `emit_gsub.py` calls `formation_blocked` to generate shipped FEA rows. Every future settlement change is therefore written twice. That recurring duplication is the port's largest real cost, and it is larger than the build cost within a few quarters.
- **Three author-facing things break and must be budgeted.** `explain.py` and `probe.py` exist to replay _the same code that built the table_, and would start explaining what Python thinks rather than what the font was built from; `settle.py`'s `_incomparable_message` prints a paste-ready YAML resolve stub from inside the kernel; and `uv sync` stops being sufficient to reproduce the project.
- **Rust, not Go.** Rust's multiplier holds flat across problem size while Go's decays as its collector scans a larger live heap. A memoized fixpoint whose entire working set stays live until the build ends is the worst shape for a tracing collector and the best for an arena.
- **The packing is the win, not the language.** CPython beats both Rust and Go at inserting a ten-slot _string_ key, because it caches string hashes. The same insert on a packed integer key is an order of magnitude faster. A port that keeps string keys forfeits most of the prize. Relatedly, a fast non-cryptographic hasher without a proper finalizer was measured _far slower_ than SipHash on these keys, whose low bits are a five-value alphabet.

## What a port does not fix

At the full alphabet the conformance sweep is Θ(alphabet^horizon), and no language change touches that exponent. `--conform-horizon` and the calt sweep's `max_chars_after` are coverage decisions that move numbers a rewrite cannot reach. Persisted liveness verdicts are the other lever of this kind. If the port happens, these are still needed; if the exponent keeps rising, they matter more than the port.

## Open questions

- **Can `TraceShare` be made value-keyed**, so private per-thread specs share the donor's memo? Its keys look value-comparable on inspection, but `FeatureSensitivity` is built from `self.spec` and `settle._guard_state` is keyed on `id(spec)`. If it works, parallelism and the share compose instead of excluding each other, and both the threaded-Python and parallel-Rust numbers rise substantially. This is the single highest-value experiment left and it is small.
- **Do the lever patches survive the full rebuild suite?** Only the kernel tests were run against them, never `make test-rebuild`'s full set.
- **The model spec's condition contents are synthetic.** Its shape is copied from the real spec by introspection, but the conditions come from a seeded PRNG, and real rune family masks have structure a PRNG does not. This is the largest residual uncertainty in the multiplier and it cannot be closed without writing the real port.
- **The real build cadence has never been measured**, so every per-day figure is a model of intended workflow rather than observed behaviour.

## Disposal

Per the repo's note-taking rules, checked-in evidence lives only as long as its decision is open. When the port is either built or ruled out, delete `evidence/` — git preserves it — and keep whichever harnesses are still load-bearing. `scaling/` outlives the decision either way: it is the migration's early-warning system.
