# Shaping-leak investigation: findings and recommended strategy

This note records a prototype investigation (branch `prototype/leak-checker`) into making OpenType shaping/isolation leaks impossible — or at least catchable without hand-writing `44^n` tuple tests. It is evidence first, recommendation second. The shipped gate is `tools/leak_snapshot.py` + `test/isolation-leak-snapshot.txt`; the FEA parser used to reach the conclusions below is the (kept, reusable) `tools/leak_static_analysis.py`; the one-off sweep/validation scripts are throwaway in `tmp/` (gitignored).

## What a leak is

A non-joining adjacent pair (e.g. across a word boundary) whose chosen glyphs or positions differ when the pair is shaped together vs. when each half is shaped alone. The invariant is `test/test_shaping.py::_check_break_isolation`; the sweep is `tools/build_check_html.py::find_leaks`. A non-join is exactly `exit_ys(left) & entry_ys(right) == set()`.

## Measured facts

- **The current CI gate is blind to most leaks.** `test/test_isolation_leaks.py` runs `find_leaks(max_len=3)` and asserts zero visible (`diff`-classified) leaks. At depth 3 there are **0**. At depth 4 there are **387** visible leaks (`51 s` to compute), none of which any test sees. The two most recent commits on `master` hand-fix `·Utter·Gay·Tea·Oy` and `·Utter·Gay·Thaw·-ing` — yet `·Ah·Gay·Tea·Oy`, `·Ah·Gay·Thaw·-ing` and hundreds of structurally identical cases still leak. This is the "I keep writing `44^4` tests" treadmill, quantified.
- **The emitted Senior `calt` is 14 MB / ~19,700 single-pivot contextual rules**, max backtrack 2, max lookahead 2 (`tools/leak_static_analysis.py` parses it cleanly). ~600 lookups, applied in sequence.
- **Leaks cluster by mechanism.** Of the 387 depth-4 leaks: 245 (63%) are "the left glyph's _exit_ form changes because of a follower that has no entry" — i.e. a glyph's exit is modulated by a follower it cannot join. The shape deltas are overwhelmingly exit/entry modifiers (`ex-y0`, `before-may`, `noexit`, `en-y0`/`ex-y5`). Many are intentional cosmetic tucks (`before-may`, `before-vertical`) that deliberately break strict isolation — which is why some are accepted (`|?|`) and some are bugs.

## What does NOT work: a sound static leak-checker over the FEA

The appealing idea (parse the FEA, prove no rule's match window can span a non-join) **fails**, for two structural reasons established empirically:

1. **Leaks are emergent across lookups, not properties of single rules.** Worked example `·Ah·It | ·Tea·Oy`: `qsIt` keeps its `en-y5.ex-y0` form in isolation but reverts to bare in context. There are **zero** rules mentioning both an `It`-form and `qsTea_qsOy` anywhere. The leak emerges from the backward `It` upgrade (no lookahead) + a separate exit-dangle revert + the `Tea`+`Oy` ligature, interacting across ~600 chained lookups. You cannot find a leak by scanning for a rule that contains both sides — there isn't one.
2. **No fixed `max_len` is provably complete.** A rewrite of glyph _Y_ (driven by _Y_'s own context) can feed glyph _X_'s rule, so influence chains past the ±2 single-rule window. The effective dependency window is bounded only by join-chain length, which a non-join is _supposed_ to terminate — but only does so if the emitter guarantees it. This is precisely why hand-enumeration never converges.

A per-rule "pivot changes due to a non-joining neighbor" predicate (both `sub` upgrades and `ignore` suppressions) reaches only ~64% recall on depth-4 leaks while flagging **56,000** candidates (~1% precision). Pushing recall to 100% would require simulating chained-GSUB execution — i.e. reimplementing the shaper. Static analysis of the emitted FEA is a dead end.

## What works now: an approved-snapshot regression gate

Replace hand-written tuple tests with a golden snapshot of the _complete_ depth-4 leak set, keyed by structural signature `(isolated_left, left_chosen, isolated_right, right_chosen)` — 387 stable signatures. The gate becomes a set diff:

- a **new** signature not in the approved set → fail (regression: you introduced a leak);
- a signature that **disappeared** → bless the snapshot (you fixed one).

This retires the `44^n` treadmill: you never hand-write tuples; review surfaces exactly which cross-break interactions changed. It is strictly better than depth-3 + hand tuples, and the existing `find_leaks` machinery already produces it. Limitation: depth-4-bounded, so it is a strong regression gate, not a completeness proof — deeper emergent leaks can still escape, but every change's _delta_ is now visible.

Cost: depth 4 is ~51 s (too slow for the default `make test`; fits a dedicated target / pre-push / nightly). Reachability pruning could cut this substantially.

## The real prevention: a construction-time invariant (design, not yet built)

Because the dominant class is "exit/entry form chosen on a non-joining neighbor", the leak-free property belongs in the **emitter**, not a post-hoc checker. The direction: every contextual form declares the exact neighbor context it requires (a "context contract"), and the emitter refuses to select an exit/entry variant for a neighbor it does not cursively join — except where the form is explicitly an approved cosmetic cross-break tuck, which then auto-registers in the snapshot above. That makes the dominant 63% structurally impossible and turns the rest into a reviewed, enumerated set rather than a discovery problem. This is the high-effort, high-payoff path (depends on splitting the 4,000-line `_emit_quikscript_calt` into typed passes first) and should be chosen deliberately.

## Recommended sequencing

1. **Now (low risk):** adopt the depth-4 approved-snapshot gate; triage the 387 once into approved-cosmetic vs. bug; fix the bugs. Kills the hand-enumeration pain immediately.
2. **Next (medium):** split `_emit_quikscript_calt` into typed passes (byte-identical FEA, gated by `make snapshot-before` + diff) so the emitter is instrumentable.
3. **Then (high payoff):** add the context-contract invariant so the dominant leak class is impossible by construction; the snapshot from step 1 becomes the approved-cosmetic allow-list.
