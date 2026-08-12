# Rust port of the M1 settlement kernel — plan

Decided — the port is being built. [Issue #40](https://github.com/adiabatic/abbots-morton-spaceport/issues/40) is the tracker; its sub-issues, in dependency order, carry the milestones and the session-to-session working state, and this file stays the design-facts home. It deliberately does not carry measurements: the harnesses beside it regenerate those, and a number written here would be stale the moment the next change lands.

## The decision

Settled: `rebuild/pipeline/table.build_tables` and its settlement engine are being reimplemented in Rust as a standalone binary, kernel-only, tracked in issue #40. The tracker closes at cutover — the Rust engine dual-runs byte-identical against Python through two full letter migrations after integration, then becomes the engine of record.

**It is not a seconds-per-day decision.** The port's incremental value over the keep-the-Python stack is real but modest. What settles it is alphabet headroom: the fixpoint grows steeply in the alphabet — `scaling/scaling.py` is the live authority for how steeply — and the target is all 44 codepoint-bearing letters.

**Read the exponent in a stated denominator, always.** `scaling.py` prints its consecutive-pair exponents against _runes_, and a rune exponent is the letter exponent multiplied by `d ln letters / d ln runes`, which the nested ladder drives from below 1 to above 1 as it stops adding ligatures and starts adding letters. Quoting a rune-denominated figure as a per-letter one overstates the 44-letter multiplier severalfold, and the ladder's letters-to-ligatures mixture is the _inverse_ of the migration's, so the two bases bracket rather than agree. Fit the whole ladder, say which denominator, and treat the apparent rise across consecutive pairs as a property of which letters that rung added rather than of how many.

**The user's rationale, recorded:** all 44 letters eventually. That is what makes the port a _when_ rather than an _if_ — and also what makes it insufficient on its own, because at 44 letters even a parallel port lands near the edge of tolerance, and only at the optimistic end of both the multiplier and the exponent bands.

## Sequence

1. **The Python levers first**, and not mainly for their own sake. They move the denominator every port measurement is a ratio against; benchmarking a port against the pre-lever kernel overstates its value by the whole stacked lever factor, which `levers/m1_all_configs.py` re-measures against a tree built by `levers/mktree_at.sh`. They are also what keeps the next few migration batches bearable. **Landed**: the live `run_m1` batch freezes its inherited heap and disables cyclic GC, and the settlement memoizations are in the shipped kernel. `levers/apply_m1_patches.py` now holds only the two patches deliberately _not_ taken — a twelve-element memo key that cost more than the call it replaced, and one inside the noise — and it aborts against the live tree because its anchors landed. That abort is correct; do not re-anchor it to make it run.
2. **Re-baseline.** Done on `restoration.local`: the journal exists, the endpoints, the sweep and `kernel-model/run.sh` were all re-run after the levers landed, and `evidence/raw/perf2/verify-headline/classify_commits.py` re-derives the arming rate from the git history rather than assuming it. Re-run the endpoints and the sweep after each migration batch; `make cycle-timings` is the live authority for the cycle itself.

**The kernel model's job is done.** The fidelity drift this step flagged — the real kernel memoizes `candidates` and `transition_trace`, the model does not — is retired along with the projection it fed: the decision no longer rests on a modeled multiplier, and the port is measured against the real kernel through the differential contract (the boundary sub-issue under the tracker), not projected from the model.
3. **Decided.** Issue #40 and its sub-issues are the plan of record; the thresholds below stay as the record of what the argument turned on.

## Thresholds that settled it

The decision is made; this table is its record, and two rows keep live jobs. The whole-ladder exponent stays the migration's early warning — re-run `scaling/scaling.py` after each batch, because a port moves the constant and not the exponent, so a steepening ladder makes work avoidance due regardless of language. And the kernel-semantics cadence row prices the write-it-twice tax the port's standing differential gate exists to police.

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

- **The cut line is `build_tables`, not the existing subprocess.** `run_m1` looks like a boundary but also mints glyphs, runs defect gates, emits GSUB/GPOS, compiles the font with fontTools and runs three HarfBuzz gates. One level in, a JSON dump of the resolved spec carries the input side — but the windows file alone cannot carry the output side: `_rules_for_input` reads per-transition provenance and joints, and the treaty fold reads settled fields the TSV drops. **A binary that reads a spec dump and emits the enriched transition stream — the full `Transition` grain plus fired provenance, deep classes and reachable cells — with Python keeping `_rules_for_input`, the treaty fold and the artifact writers. No FFI; and because the writers never change hands, byte-identity of the persisted artifacts stays the contract.**
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

- **Does cross-configuration sharing still pay at port speed?** In Rust the share can be value-keyed from the start — the thing the `id(spec)`-keyed Python plumbing could not be — so parallelism and sharing compose instead of excluding each other; whether the share is worth keeping at all is measured in the make-it-fast sub-issue under the tracker.
- **What did the class-grain deep slots actually buy?** Measured as a one-flag A/B (`AMS_DEEP_CLASSES=0` against the default), the collapse is real in rows and growing with the alphabet, but it does not reach the clock at today's size: per-window cost rises by almost exactly the collapse factor. The estimate that motivated it was an order of magnitude larger than what the ladder shows, so the case for it should be restated in terms of what fewer rows are worth downstream — a smaller memo to persist, fewer rows for the conform sweep and the review surface — which is real and is not measured anywhere yet.
- **Does the ladder's exponent describe the migration's?** The nested subsets freeze the ligature count partway up, so letters outrun runes; the migration does the reverse. Nothing measures a large alphabet's ligature load, and the two bases differ by several-fold at 44 letters. Counting reachable cells for the target alphabet from the registry would replace the widest guess with an estimate, and costs no machine time: cells predict windows, and windows predict CPU almost linearly.

## Disposal

Per the repo's note-taking rules, checked-in evidence lives only as long as its decision is open. The build is underway; the cutover sub-issue under #40 deletes `evidence/` — git preserves it — and keeps whichever harnesses are still load-bearing. `scaling/` outlives the port either way: it is the migration's early-warning system.
