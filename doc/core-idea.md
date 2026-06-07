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

## Don't-care is the default — and it's discovered, not declared

For ordinary running text, most joins are don't-care. A sequence of four or five letters might carry one or two strong requirements (Manual-mandated), and the author has no wish to micromanage the rest. A standing global preference colors the don't-care space: **all else equal, more joins are better than fewer.**

Crucially, the author **cannot reliably pre-classify** a pair as don't-care vs. do-care from the outset. The classification *migrates*: a pair sits in don't-care until, one day, the author looks at a result and decides they do in fact care. So the spec must not demand an up-front verdict for every pair. Instead it must support **incremental pinning** — start permissive, and let the author promote a pair to "cared about" the moment they notice something, recording the verdict then.

This reshapes the punch list. The residue that should ever demand human attention is small:

- **Pinned (mandatory) breakage** → never a punch-list item. Caught and rejected by automated tests. (A pin *may* occasionally be negotiable — perhaps satisfiable only by enabling an otherwise-undesirable stylistic set for part of it — but relaxing a pin is a deliberate, rare act the author takes only after watching an agent struggle at length and give up. It is never the default.)
- **Objective defects** → never a punch-list item *for the human*. Detected automatically so an agent can fix them. (See below — this is the author's single biggest time sink.)
- **A previously-blessed discretionary pair that changed** → this, and essentially only this, is the legitimate punch list.

Don't-care pairs change silently. The author pays attention only to pairs they have already chosen to care about, plus machine-found defects.

## The real job: cleanly express what each letter may and may not do

The author's sharpest complaint about the current design: it **does not cleanly express what a given letter may do and may not do.** This is where most "Constrained but free" debugging goes, and the rebuild's central task is to fix it.

A letter's join behavior is governed in large part by **how the letter is physically written**, not by taste. These are structural capabilities, e.g.:

- A given letter can be *joined to* at the x-height and *exit* at the baseline, **or** the reverse (entry baseline, exit x-height) — but it **cannot** join-and-exit both at the x-height, **nor** both at the baseline… *unless a particular stylistic set is enabled*, which unlocks the otherwise-illegal combination.

So each letter (and each variant) has a **join surface**: which entry heights it can accept, which exit heights it can offer, and which *combinations* of the two are simultaneously legal — with stylistic sets able to unlock combinations that are otherwise forbidden. If this surface is modeled cleanly and honestly, two big wins follow: the shaper can never select a variant that reaches for an attachment its neighbor can't provide (that whole class of defect becomes impossible by construction), and the genuinely free choices are clearly separated from the structurally forbidden ones.

## Contextual preference is a first-class, common pattern

A very common shape of "constrained but free": a form has a **preferred-in-isolation** default that should yield when it improves the surrounding joins. Worked example: `·It·No.alt` is preferable to plain `·It·No` on its own, **but** if plain `·It·No` makes the wider context better — it sets up a preceding baseline join into ·It, or a subsequent x-height join out of ·No — then plain `·It·No` should win. The spec must express "prefer X in isolation, but switch to Y when Y buys a better neighbor join," and it must do so without bespoke per-case machinery.

## Defects must be found by the machine, not the eye

Defective pairs are the **lion's share** of the author's debugging time. Automatically *detecting* them — so an agent (or the author) can fix them — is a top-priority requirement, arguably co-equal with readability. The two named defect archetypes so far:

- **Collision / false stroke:** near-vertical letters set adjacent so the pair reads as one thick stroke.
- **Reaching join with no acceptor:** a variant shaped to join at a height the neighbor can't accept.

Both are derivable from honest letter-capability and geometry data; neither should require the author to spot it by eye.

## The unit of authoring is the written variant (repertoire-first)

The spec is **variants-first** (model B), not capability-matrix-first. A letter is authored as a small, **closed, explicitly-declared repertoire of written forms** — the genuine ways a hand would draw it. ·May, for instance, can be written counterclockwise or clockwise, and each needs its own bitmap to look right. The author wants to state plainly that ·May has *only these N ways* of being written and joined — no more.

Two qualities are essential and in tension:

- The repertoire is **finite and named** — you can read off a letter's complete set of forms and know that's all of them.
- The repertoire **evolves**. As the font is polished to more faithfully approximate what a real Quikscript writer would do, forms are added, refined, or retired. "Closed" means *fully enumerated right now*, not *frozen forever*.

The legal join surface (which entry/exit heights and combinations a letter supports) is then **read off** the repertoire and surfaced to the author, rather than being declared independently. This keeps the readability win of the heights-first view (model A) as a *derived, displayed* artifact while keeping authoring grounded in real written forms.

**Open tension to resolve next:** variants-first *is* essentially today's model, and today's pain is exactly the accretion of forms. So the rebuild's success hinges on a principled answer to: *what makes a form a legitimate member of the repertoire (a real way to write the letter) versus an accretion (a form that exists only to patch one join bug)?* Without that line, "mostly B" risks walking straight back into the local maximum.

## Attachment heights

The join vocabulary is small. The attachment heights that matter:

- **baseline**
- **x-height**
- **y=6** — just above, used (as far as the author knows) only to connect *to* ·Ye from a handful of letters that connect to the *next* letter at the x-height
- **top** — e.g. ·See·Tea joins

## Reaching often requires deformation — and deformation must be controlled

Matching attachment *heights* is necessary but not sufficient. To connect to an otherwise-awkward attachment point, a form sometimes has to be **deformed**: a stroke extended here, contracted there, so it reaches. This is its own axis of capability, distinct from "which heights does the form offer."

This is also a current bug source: getting an LLM to extend *exactly* the things that should extend — and to leave alone the things that shouldn't — is unreliable. The author's preferred remedy is workflow, not just data: the tooling should **ask the author** when an extension or contraction is in question, rather than guess. (This generalizes the standing project rule to ask when multiple valid choices exist.) Whether a deformation is a distinct repertoire member or a separate adjustment layer is a question for the next round.

## What a form is — and the category error behind the accretion

The author's definition: **a form belongs in a letter's form list if and only if it specifies a bitmap together with everything that is possible with it and how it should join to other things.** A form is a self-contained statement of one genuine way to write the letter, plus its full join capability.

The accretion is a **category error against that definition.** Form lists bloat because minting a form is, today, the most convenient way to express something that isn't a *way of writing the letter* at all — it's a **contextual join override**, most often a *suppression*: "in this particular case, don't join in *this* manner, even though it would otherwise be permissible." The form exists only to carry that override. Such forms are named after the *context that birthed them* (`*.before-day-exam`, `*_after_it_and_vie`, `*.ex-noentry`) rather than after a way of writing the letter — a reliable tell.

So the override complexity is real and (the author believes) **irreducible** — the domain plus OpenType's limits are genuinely that complex. The goal is not to delete it but to **house it correctly.** The author already tries to push intelligence down into the Python; what can't go there has accreted into long override lists in `quikscript.yaml` that "smell like warts."

### Where the pressure goes when forms stop carrying it

Decompose what an accretion-form is currently doing into its real parts, each with a proper home:

- **A genuinely different written shape** → stays a form (it has its own bitmap; by the definition above it *must* be a form). But its *triggering context* must not be baked into its identity or name.
- **The same shape, deformed to reach** an awkward attachment → the **deformation** axis (extend/contract), not a new form.
- **The binding of form-to-context** ("when does this form apply") and **pure suppression** ("don't join ·X·Y this way / at all," with no shape change) → relational join rules **co-located on the letters themselves** (see "Locality of reference" below), expressed over the clean repertoire — not carried by minting new forms.

**Gut check — `qsNo.forms.alt_after_it_and_vie`:** a specialization of `qsNo.forms.alt` via inheritance (inheritance is a genuinely good idea and stays). It is *not* about a "tighter" shape, as first guessed. It exists because **·It and ·Vie only connect at the baseline *sometimes*** — the predecessor's baseline exit is *conditional*, and ·No must select a form that matches it when (and only when) that conditional exit is present. So the real content is **selection conditioned on the neighbor's state**, not a new way of writing ·No. It's currently a separate form only because adding one was the least-bad place to put that conditional given the current YAML structure. The rebuild's job is to let this condition ride on an *existing* form rather than spawn a sibling named after the neighbors that summon it.

## Locality of reference: at most two places

This is a hard constraint, decided. To understand whether and how a pair of letters joins, a reader should look in **at most two places: the left letter (its bitmaps and forms) and the right letter (its bitmaps and forms).** Nothing else. There is **no separate relational file** — co-located policy wins decisively over a standalone relational layer, because a third lookup site breaks locality.

Consequences:

- **Suppression rides on one of the two letters.** "·Way·Thaw must never join" lives on either ·Way or ·Thaw, with a **weak preference for the lead (·Way)** — both because that matches how the author thinks and because it mirrors how OpenType operates left-to-right by default.
- A relational rule is therefore a rule the *letter* owns about its neighbors, not a free-floating pair object. The set algebra and conditional-selection language exist to let an *existing* form (or the family) carry these rules cleanly, so contextual overrides stop minting forms.
- **One sanctioned exception:** kerning may live separately if doing so unlocks better tooling — e.g. pasting kerning data into a `<textarea>` and editing it in a small web app. Convenience of bulk editing can override strict locality for kerning specifically; the default for everything else stays two-place.

## Two missing pieces the layering must account for

- **Kerning** is a real dimension not yet discussed. It's currently stored separately, for reasons "some very good, though maybe not dispositive." Per the locality exception above, keeping it separate is the one sanctioned break from two-place locality — justified by bulk-editing tooling rather than by the join model. Its exact home is still **open**.
- **Set algebra is underused.** The YAML has set *union* (a context set can include other context sets) but no set *subtraction*. Much override complexity is really "this set, minus those" expressed the long way (or by minting a form). A policy language with first-class **union and subtraction** over named sets would absorb a lot of what currently forces new forms.

<!-- Interview in progress: more sections to come. -->
