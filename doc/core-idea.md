# Core idea

This document is the north star for a from-scratch rebuild of Abbots Morton Spaceport: a Quikscript Senior font generated from a YAML specification. It is being written by interview. It records *what the system is for* and *what makes a design good or bad* — not how to implement it.

## What we're rebuilding, and why

The end goal is unchanged from the current repo: a Quikscript Senior font whose letter shapes and — most importantly — whose **joins** (which pairs join, which don't, and what each join looks like) are driven by a human-authored YAML spec. A program reads the spec and emits a font that matches it.

What's changing is the *character* of the spec. The current system was built before its author understood the problem space, so it grew organically and accreted. That accretion is the primary wound the rebuild must heal.

### The wound: accretion, and the verification slog it causes

The author's own diagnosis, in priority order:

1. **Accretion is the biggest pain.** Every new join problem got its own bespoke escape hatch (`strip_entry_before`, `_PENDING_LIGA_ENTRY_GUARDS`, `noentry_after` propagation, scoped-anchor selectors with `except`, post-liga cleanup passes, and so on). There is no small set of orthogonal concepts — it's a pile of special cases. The current design may be a local maximum that's hard to climb out of.

2. **The concrete, day-to-day pain is verification cost.** The system doesn't feel like it's *fighting* — the author isn't afraid to touch it, partly because the domain is largely (not totally) amenable to automated testing. The pain is that it has become a **slow slog**: after a change, the author must manually verify that nothing *else* broke, hand-building a punch list of "this is wrong, this is right, this is fine either way" per affected pair.

3. **Unpredictability bites somewhat** — small source changes (a widened selector) can ripple into other pairs — but it's secondary to the accretion and the verification cost it imposes.

So the rebuild is not primarily about fear or friction in *making* a change. It's about the cost of *trusting* a change. A good design makes the blast radius of an edit cheap to see and cheap to verify.

<!-- Interview in progress: more sections to come. -->
