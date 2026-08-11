# Rust port of the M1 settlement kernel — plan

An open fork. This file holds the decision, the thresholds that settle it, and the design facts that came out of measuring it. It deliberately does not carry the measurements themselves: the harnesses beside it regenerate those, and a number written here would be stale the moment the first lever lands.

## The decision

Whether to reimplement `rebuild/pipeline/table.build_tables` and its settlement engine in Rust, as a standalone binary reading a serialized spec and writing the same windows file.

**It is not a seconds-per-day decision.** The port's incremental value over the keep-the-Python stack is real but modest. What settles it is alphabet headroom: the fixpoint grows steeply in the alphabet — `scaling/scaling.py` is the live authority for how steeply — and the target is all 44 codepoint-bearing letters.

**Read the exponent in a stated denominator, always.** `scaling.py` prints its consecutive-pair exponents against _runes_, and a rune exponent is the letter exponent multiplied by `d ln letters / d ln runes`, which the nested ladder drives from below 1 to above 1 as it stops adding ligatures and starts adding letters. Quoting a rune-denominated figure as a per-letter one overstates the 44-letter multiplier severalfold, and the ladder's letters-to-ligatures mixture is the _inverse_ of the migration's, so the two bases bracket rather than agree. Fit the whole ladder, say which denominator, and treat the apparent rise across consecutive pairs as a property of which letters that rung added rather than of how many.

**The user's rationale, recorded:** all 44 letters eventually. That is what makes the port a _when_ rather than an _if_ — and also what makes it insufficient on its own, because at 44 letters even a parallel port lands near the edge of tolerance, and only at the optimistic end of both the multiplier and the exponent bands.

## Sequence

1. **The Python levers first**, and not mainly for their own sake. They move the denominator every port measurement is a ratio against; benchmarking a port against the pre-lever kernel overstates its value by the whole stacked lever factor, which `levers/m1_all_configs.py` re-measures against a tree built by `levers/mktree_at.sh`. They are also what keeps the next few migration batches bearable. **Landed**: the live `run_m1` batch freezes its inherited heap and disables cyclic GC, and the settlement memoizations are in the shipped kernel. `levers/apply_m1_patches.py` now holds only the two patches deliberately _not_ taken — a twelve-element memo key that cost more than the call it replaced, and one inside the noise — and it aborts against the live tree because its anchors landed. That abort is correct; do not re-anchor it to make it run.
2. **Re-baseline.** Done on `restoration.local`: the journal exists, the endpoints, the sweep and `kernel-model/run.sh` were all re-run after the levers landed, and `evidence/raw/perf2/verify-headline/classify_commits.py` re-derives the arming rate from the git history rather than assuming it. Re-run the endpoints and the sweep after each migration batch; `make cycle-timings` is the live authority for the cycle itself. **What that re-run exposed is on the next line, and it matters more than any of the numbers.**

**The model has drifted out of fidelity with the kernel it models, and the port multiplier is no longer safely derivable from it.** The three model implementations still agree exactly — same window checksums, same four call counters, share still answer-preserving — so the Rust-against-Python ratio is still a sound measurement of _the model_. What broke is the calibration that converts that ratio into a claim about the real kernel. The real kernel now memoizes `candidates` and `transition_trace`; the model does not. So the two sides of the fidelity ratio no longer measure the same thing: nearly all of the real kernel's calls are now memo hits, while every one of the model's does full work. The arithmetic still produces a band that looks reassuringly like the one it replaced, and that resemblance is a coincidence of two errors pointing opposite ways — do not read it as confirmation. **Give the model the same memo before quoting a discounted port figure**, or quote the model ratio undiscounted and say that is what it is.
3. **Then decide**, against the thresholds below.

## Thresholds that settle it

Check these rather than re-litigating the argument. Each is a re-run, not a judgement.

| Threshold | How to check | What it means |
| --- | --- | --- |
| Alphabet target passes ~18 letters | the migration itself | Every keep-the-Python path is exhausted; only a port reaches further |
| Six-config cold wall passes ~600 s | `levers/m1_all_configs.py --mode fresh`, or `make cycle-timings`'s `build_tables_total` | The levers are spent |
| Whole-ladder exponent rises above ~4.5 **in letters**, or ~5.5 in runes | `scaling/scaling.py` after each migration batch, fitted over every rung | Even a parallel port stops reaching 44; work avoidance becomes mandatory regardless of language |
| Peak RSS at the top rung passes ~half the box | `scaling/scaling.py`'s `rss_gb`, and `m1_all_configs.py`'s `peak_rss_gb` | Memory, not wall clock, becomes the binding constraint — and a port moves that constant without moving its exponent |
| Kernel-semantics commits fall below ~1/quarter | `git log -- rebuild/pipeline/{settle,table,model,specificity}.py` | The duplication tax collapses and the port gets much cheaper |
| `TraceShare` can be made value-keyed | see the open question below | Parallelism and the share stop excluding each other; every parallel figure rises |

The exponent check is the cheapest and the most decisive, and it is the one nobody was running. Re-run it after each migration batch. Two cautions, both learned by getting them wrong: fit the **whole ladder** rather than quoting a consecutive-pair figure, because a single pair is sensitive enough to ordinary run-to-run scatter to swing by a large fraction of the threshold, and which letters that rung happened to add moves it further than how many did; and state the denominator, per the decision section above. The letters and runes thresholds in that row are the same threshold, not two.

The alphabet row is stated against the historical six-config budget and moves with it — at a 20-minute tolerance the keep-the-Python paths reach several letters further, which is why "what wall are we willing to wait" is worth deciding explicitly rather than inheriting.

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
- **The model spec's condition contents are synthetic.** Its shape is copied from the real spec by introspection, but the conditions come from a seeded PRNG, and real rune family masks have structure a PRNG does not. This was the largest residual uncertainty in the multiplier; the memo drift above has overtaken it, and unlike this one that drift is cheap to close.
- **Should the model track every kernel memo, or should the calibration stop going through call counts?** Adding each memo to the model keeps the counters comparable but makes the model track the kernel forever, which is the same duplication tax the port itself is charged for. The alternative is to calibrate on something the memos cannot move — cost per window, say — and accept a coarser anchor. Whichever is chosen, `real_kernel.py`'s counted pass (`K1_COUNT=1`, whose output belongs in `kernel-model/real-kernel-counters.json` so the uninstrumented timing pass can still report fidelity) is the thing to keep honest, because a run without it silently reports no fidelity at all rather than failing.
- **What did the class-grain deep slots actually buy?** Measured as a one-flag A/B (`AMS_DEEP_CLASSES=0` against the default), the collapse is real in rows and growing with the alphabet, but it does not reach the clock at today's size: per-window cost rises by almost exactly the collapse factor. The estimate that motivated it was an order of magnitude larger than what the ladder shows, so the case for it should be restated in terms of what fewer rows are worth downstream — a smaller memo to persist, fewer rows for the conform sweep and the review surface — which is real and is not measured anywhere yet.
- **Does the ladder's exponent describe the migration's?** The nested subsets freeze the ligature count partway up, so letters outrun runes; the migration does the reverse. Nothing measures a large alphabet's ligature load, and the two bases differ by several-fold at 44 letters. Counting reachable cells for the target alphabet from the registry would replace the widest guess in the projection with an estimate, and costs no machine time: cells predict windows, and windows predict CPU almost linearly.
- **The per-day figures rest on a cadence that is only partly observed.** The journal now records real M1 builds on this machine and the arming rate is re-derived from git rather than assumed, but the instrument only sees cycle-driver spawns — an interactive `run_m1` is invisible to it — so the observed rate is a lower bound.

## Disposal

Per the repo's note-taking rules, checked-in evidence lives only as long as its decision is open. When the port is either built or ruled out, delete `evidence/` — git preserves it — and keep whichever harnesses are still load-bearing. `scaling/` outlives the decision either way: it is the migration's early-warning system.
