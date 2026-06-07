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

## Where the authority for "correct" lives

There is no single oracle. Correctness has **tiers**, and they have different sources of truth — this is central, because the verification slog comes from treating all joins as one undifferentiated mass.

1. **Mandatory joins — the canonical document.** The Quikscript Manual encodes nigh-mandatory joins via `data-expect` attributes. A core *demonstration goal* of this font is to prove that OpenType tooling can produce a font that joins **exactly like the canonical example document does**. For these pairs, correctness is objective and external: the font is right iff it matches the Manual. This tier should be machine-checkable against the Manual corpus, not eyeballed.

2. **Objective defects — joins that are simply broken.** Even within the permitted rule set, the shaping system can currently produce results that are *obviously* wrong, e.g.:
   - Two near-vertical letters set immediately adjacent, so the join reads as one extra-thick stroke rather than two letters.
   - A letter drawn in a variant *specially shaped to join* at a particular height to its neighbor, when that neighbor isn't set up to accept a join at that height — a join that "reaches" for an attachment that isn't there.
   These are not matters of taste. A good design should make them either structurally impossible or automatically detected — never something the author has to catch by eye.

3. **Discretionary joins — taste.** A large space remains where a join is fully *permissible* under the rules but the author has chosen to **disallow** it anyway, because it would be awkward to write by hand or simply looks ugly. Here the authority is the author's judgment. The punch-list verdicts map onto these tiers: "wrong" = a violated mandatory join or an objective defect; "right" / "fine either way" = a discretionary call that matches taste or is simply acceptable.

The "fine either way" verdict is significant: for some pairs, *more than one* outcome is acceptable. A spec that pins every pair to one exact result would cry wolf on changes that are actually fine; a spec that pins nothing misses real regressions. The rebuild must let the author say how much each pair is *pinned* vs. *free*.

<!-- Interview in progress: more sections to come. -->
