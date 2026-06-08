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

## The load-bearing column: kill the whack-a-mole

Failure here is not binary, but there is one outcome that would make the whole rewrite a waste: **if, after all the work, the font is still about as flaky and still demands lots of manual whack-a-mole to *notice* and forbid ugly, the effort failed.** Note where the cost actually is — it's the **noticing**, even more than the forbidding. Forbidding a surfaced ugly join is cheap; *hunting* for it across the corpus is the slog.

So the single success metric the whole design must serve: **the machine does the noticing, and the human only judges what's put in front of them.** The defect detector finds all the broken; the review surface puts every relevant change in your face; you never have to go spelunking to discover that something quietly got worse. Every other decision in this document is in service of that column — if a choice doesn't reduce manual noticing, it isn't paying rent.

## The OpenType ceiling is a hard wall

The artifact is **a real font people can use on computers today**, through the font-to-monitor pipeline people actually have — not an idealized one. So **OpenType is a permanent constraint, not an implementation detail.** If OpenType can't shape it, the author can't have it, and it **must not exist in the spec at all.** Every capability, join, veto, deformation, stylistic set, and pin in this document is implicitly bounded by what OpenType (GSUB/GPOS — `calt`, `liga`, `ssXX`, anchors) can actually produce. The author has been lucky: most of what he wants has turned out to be reachable within those limits. But the limits are law, not friction — a desired join that OpenType can't shape is simply off the table.

## Greenfield encoding, sacred cargo

"Recreate it totally differently" means the **encoding** is thrown out — the YAML structure, the accreted forms and override lists, the Python/FEA pipeline machinery. What crosses the chasm essentially unchanged is the *content and the concepts*:

- **Anchor points** — what OpenType uses for `liga`/attachment.
- **The Manual corpus and its `data-expect` minilanguage** for expressing which joins must happen at which heights.
- **Attachment-height and anchor concepts.**
- **Nearly all the drawn bitmaps** — a few more may be drawn; notably, **no bitmap is generated algorithmically from another bitmap anymore** (the base bitmaps are all hand-drawn primitives; deformation parameterizes geometry on top, but doesn't derive one base shape from another).
- **All the ductus information** (see below).
- **The set of letters**, which is complete for Quikscript.

So the sacred things are assets and ideas; only their current *encoding* is up for reinvention.

### The repertoire's truth is held jointly by ductus *and* bitmaps — and finishing the ductus gates the rewrite

The repertoire-first model rests on each letter's full set of ways-of-being-drawn, and that set's truth lives in **two co-equal sources**, neither subsuming the other:

- the **ductus** — the enumeration of *how* the letter is drawn (the abstract strokes and orders, the count of distinct ways), and
- the **bitmaps** — the concrete pixels that realize those ways, canonical in their own right (and, recall, never derived one from another).

The ductus is currently **woefully incomplete**, and the author wants to **finish writing all of it before starting the rewrite** — because the failure mode is precise and dangerous: you write down four ways to draw a glyph and forget the fifth. A repertoire can only be honestly "closed" (the property the whole authoring model depends on) if the ductus that enumerates it is complete. So **completing the ductus is the gating precondition** for the rebuild — not because ductus is the *sole* source of truth (the bitmaps are equally canonical), but because it is the *enumeration* that tells you the bitmap set is complete rather than secretly missing a fifth form.

## The deepest principle: discovery, not declaration

Before the specifics, the principle that underlies all of them. **A great deal of this project is *discovering* what looks good, what looks bad, and what ruleset produces good-looking results** — it is not transcribing a design that already exists complete in his head. So the spec must never demand that a boundary be drawn correctly *up front*. Every important classification here is **discovered over time and promoted in place**, and the tooling should make that promotion a first-class, easy motion:

- **don't-care → do-care:** a pair you never thought about reveals, on the day you look, that you care.
- **one-off tiebreak → named case-group:** a conflict you resolved by hand recurs, and you promote the pattern into one named rule.
- **ugly-with-a-signature → broken invariant:** a recurring ugliness turns out to have a structural tell, and you promote it into the machine-checked set.
- **broken → declared-OK:** a join the detector rejects actually looks fine, and you record the exception.

This is a confirmed, deliberate design stance, not an accident of an unfinished font. The system is an instrument for *finding* the right rules, so its defaults are permissive, its boundaries are movable, and "I couldn't have known that in advance" is a supported workflow rather than a failure. Read every "default," "veto," "pin," and "forbid" below through this lens.

## Where the authority for "correct" lives

There is no single oracle. Correctness has **tiers**, and they have different sources of truth — this is central, because the verification slog comes from treating all joins as one undifferentiated mass.

1. **Mandatory joins — the canonical document.** The Quikscript Manual encodes nigh-mandatory joins via `data-expect` attributes. A core *demonstration goal* of this font is to prove that OpenType tooling can produce a font that joins **exactly like the canonical example document does**. For these pairs, correctness is objective and external: the font is right iff it matches the Manual. This tier should be machine-checkable against the Manual corpus, not eyeballed.

   **Caveat — the Manual is not self-consistent.** Reproducing it faithfully is *not* a matter of finding one consistent ruleset. The Manual writes some words one way and other words a different way, and the only way a single font can match **both** is to use **stylistic sets** — writing some words with a set enabled and others without. So the mandatory tier is inherently **configuration-dependent**: "matches the Manual" means "matches it under the stylistic-set configuration the Manual itself uses at that spot," not under one global default. This is why stylistic sets are load-bearing even for the must-have tier, not just for discretionary taste.

2. **Objective defects — joins that are simply broken.** Even within the permitted rule set, the shaping system can currently produce results that are *obviously* wrong, e.g.:
   - Two near-vertical letters set immediately adjacent, so the join reads as one extra-thick stroke rather than two letters.
   - A letter drawn in a variant *specially shaped to join* at a particular height to its neighbor, when that neighbor isn't set up to accept a join at that height — a join that "reaches" for an attachment that isn't there.
   These are not matters of taste. A good design should make them either structurally impossible or automatically detected — never something the author has to catch by eye.

3. **Discretionary joins — taste.** A large space remains where a join is fully *permissible* under the rules but the author has chosen to **disallow** it anyway, because it would be awkward to write by hand or simply looks ugly. Here the authority is the author's judgment. The punch-list verdicts map onto these tiers: "wrong" = a violated mandatory join or an objective defect; "right" / "fine either way" = a discretionary call that matches taste or is simply acceptable.

The "fine either way" verdict is significant: for some pairs, *more than one* outcome is acceptable. A spec that pins every pair to one exact result would cry wolf on changes that are actually fine; a spec that pins nothing misses real regressions. The rebuild must let the author say how much each pair is *pinned* vs. *free*.

## Don't-care is the default — and it's discovered, not declared

For ordinary running text, most joins are don't-care. A sequence of four or five letters might carry one or two strong requirements (Manual-mandated), and the author has no wish to micromanage the rest. A standing global preference colors the don't-care space: **all else equal, more joins are better than fewer.**

Crucially, the author **cannot reliably pre-classify** a pair as don't-care vs. do-care from the outset. The classification *migrates*: a pair sits in don't-care until, one day, the author looks at a result and decides he does in fact care. So the spec must not demand an up-front verdict for every pair. Instead it must support **incremental pinning** — start permissive, and let the author promote a pair to "cared about" the moment he notices something, recording the verdict then.

This reshapes the punch list. The residue that should ever demand human attention is small:

- **Pinned (mandatory) breakage** → never a punch-list item. Caught and rejected by automated tests. (A pin *may* occasionally be negotiable — perhaps satisfiable only by enabling an otherwise-undesirable stylistic set for part of it — but relaxing a pin is a deliberate, rare act the author takes only after watching an agent struggle at length and give up. It is never the default.)
- **Objective defects** → never a punch-list item *for the human*. Detected automatically so an agent can fix them. (See below — this is the author's single biggest time sink.)
- **A previously-blessed discretionary pair that changed** → this, and essentially only this, is the legitimate punch list.

Don't-care pairs change silently. The author pays attention only to pairs he has already chosen to care about, plus machine-found defects.

## The real job: cleanly express what each letter may and may not do

The author's sharpest complaint about the current design: it **does not cleanly express what a given letter may do and may not do.** This is where most "Constrained but free" debugging goes, and the rebuild's central task is to fix it.

A letter's join behavior is governed in large part by **how the letter is physically written**, not by taste. These are structural capabilities, e.g.:

- A given letter can be *joined to* at the x-height and *exit* at the baseline, **or** the reverse (entry baseline, exit x-height) — but it **cannot** join-and-exit both at the x-height, **nor** both at the baseline… *unless a particular stylistic set is enabled*, which unlocks the otherwise-illegal combination.

So each letter (and each variant) has a **join surface**: which entry heights it can accept, which exit heights it can offer, and which *combinations* of the two are simultaneously legal — with stylistic sets able to unlock combinations that are otherwise not explicitly allowed. If this surface is modeled cleanly and honestly, two big wins follow: the shaper can never select a variant that reaches for an attachment its neighbor can't provide (that whole class of defect becomes impossible by construction), and the genuinely free choices are clearly separated from the ones that simply aren't explicitly allowed.

## Contextual preference is a first-class, common pattern

A very common shape of "constrained but free": a form has a **preferred-in-isolation** default that should yield when it improves the surrounding joins. Worked example: `·It·No.alt` is preferable to plain `·It·No` on its own, **but** if plain `·It·No` makes the wider context better — it sets up a preceding baseline join into ·It, or a subsequent x-height join out of ·No — then plain `·It·No` should win. The spec must express "prefer X in isolation, but switch to Y when Y buys a better neighbor join," and it must do so without bespoke per-case machinery.

## Defects must be found by the machine, not the eye

Defective pairs are the **lion's share** of the author's debugging time. Automatically *detecting* them — so an agent (or the author) can fix them — is a top-priority requirement, arguably co-equal with readability. The two named defect archetypes so far:

- **Collision / false stroke:** near-vertical letters set adjacent so the pair reads as one thick stroke.
- **Reaching join with no acceptor:** a variant shaped to join at a height the neighbor can't accept.

Both are derivable from honest letter-capability and geometry data; neither should require the author to spot it by eye.

## The unit of authoring is the written variant (repertoire-first)

The spec is **variants-first** (model B), not capability-matrix-first. A letter is authored as a small, **closed, explicitly-declared repertoire of written forms** — the genuine ways a hand would draw it. ·May, for instance, can be written counterclockwise or clockwise, and each needs its own bitmap to look right. He wants to state plainly that ·May has *only these N ways* of being written and joined — no more.

Two qualities are essential and in tension:

- The repertoire is **finite and named** — you can read off a letter's complete set of forms and know that's all of them.
- The repertoire **evolves**. As the font is polished to more faithfully approximate what a real Quikscript writer would do, forms are added, refined, or retired. "Closed" means *fully enumerated right now*, not *frozen forever*.

The legal join surface (which entry/exit heights and combinations a letter supports) is then **read off** the repertoire and surfaced to the author, rather than being declared independently. This keeps the readability win of the heights-first view (model A) as a *derived, displayed* artifact while keeping authoring grounded in real written forms.

**Open tension to resolve next:** variants-first *is* essentially today's model, and today's pain is exactly the accretion of forms. So the rebuild's success hinges on a principled answer to: *what makes a form a legitimate member of the repertoire (a real way to write the letter) versus an accretion (a form that exists only to patch one join bug)?* Without that line, "mostly B" risks walking straight back into the local maximum.

## Attachment heights

The join vocabulary is small. The attachment heights that matter:

- **baseline**
- **x-height**
- **y=6** — used (currently, and likely forever) only for the ·See·Pea and ·Pea·Pea joins
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

## Deformation is a parametric adjustment, authored, owned by a form

(Terminology, provisional: "deformation" is being *tolerated* here to mean specifically **extension/contraction** — it's overly broad for the job, and a better word may replace it. The per-instance value is the **deformation amount**, never the "magnitude.")


Resolving the question parked earlier: for joins, a deformation is a **parametric adjustment, not a repertoire member.** ·Jay has *one* exit; "extend by 2 toward ·Exam" is a small directive (today's `extend_exit_before` / `contract_entry_after` shape) that the build applies to generate the geometry. This keeps the repertoire small — the opposite of minting a `·Jay-exit-extended-2px` form. (The "for joins" qualifier is deliberate; non-join deformations may behave differently and are parked.)

- **Trigger: authorial intent by default.** Extensions and contractions are *declared*. Some could be driven by detected need, but even then the **deformation amount** — 1px versus 2px — is an aesthetic judgment the author insists on having the final say over. The machine may *propose*; the author decides the amount.
- **Home: on a form, lead-preferred, but flexible.** Deformations live on one or more forms (locality holds — it's still one of the two letters). The author prefers to keep them on the **left** letter, but it's sometimes nicer to declare them on the **right**, and a single deformation may even be **split** across both sides, in part or in full.

### A mis-scoped deformation can be a symptom of an under-fleshed repertoire

A recurring bug class: a deformation directive lands on **not exactly the right set of ways to write a letter** — it extends the thing you meant *and also* extends something you didn't. This has two distinct root causes, and telling them apart matters:

- **Plain mis-scoping** — author or LLM error in the current YAML: the directive's target set is simply wrong, and the fix is to narrow it.
- **An under-fleshed repertoire** — the deeper case. The directive over-applies because two genuinely different ways of drawing the letter are still conflated into one form, so there's no precise target to attach to. The real fix is to **split the form** into the distinct ways it actually needs, then aim the directive at the right one.

This is a direct echo of the ductus gate: when the repertoire under-distinguishes, deformation directives have nothing precise to bind to and bleed onto siblings. Completing the ductus isn't only about *coverage* — it's what gives every directive an exact target, so "extends one thing and accidentally another" stops being possible.

### The deformation amount has a tolerance band, and "don't join" is a real outcome

A deformation amount is one of three things: **too short** (a *hard* error — typically still an off-anchor touch, i.e. still broken), **OK** (a *band*, not a single value), or **needlessly long** (a *weaker* error — tolerable but flagged). And sometimes **none** of the deformation amounts look right, and the correct fix is to **not join at all** — a suppression. So the off-anchor-contact fix is a small decision: *land the extension in the OK band, or abandon the join.*

This resolves the consistency point. The autonomous loop can rest at **OK** (or, tolerably, at *needlessly long*), never at *too short* — the band gives it room to reach a stable, shippable state without the author's pixel-precise call. The workflow the author actually wants is:

- **Deformation amounts: a fully autonomous loop** that picks values aiming for the OK band — *act-then-review*, not pause-and-ask mid-loop.
- **Plus an in-your-face review surface** that pops the resulting changes up for fast **thumbs-up / thumbs-down**. So the loop runs unattended, but *every* change it makes is surfaced for a quick binary verdict — supervised after the fact, not during.

This makes the review workflow's "opinion vocabulary" concrete at its floor: at minimum a thumbs-up/down per surfaced change, with "don't join" available as a first-class verdict when no deformation amount satisfies.

## The readability bar: local completeness, even if the length is crazy-long

The decided definition of "clearly-documented, easy-to-understand YAML": **local completeness (A) over minimal surface (B).** Reading one letter's entry top to bottom tells you everything that letter does and every join it permits or forbids. The single proviso: understanding a *pair* may legitimately require **both** letters open at once — one letter per editor pane, two panes. That is the operational form of two-place locality.

Two hard admissions:

- There is a **large amount of irreducible complexity** in what this font is trying to be — true even before OpenType, whose limitations must be papered over at every step, adds its own fighting on top. The rebuild does **not** promise short or simple YAML.
- **Long single-letter entries are accepted** — nose held — as the least-bad option. Entry length is therefore **not** evidence of a design failure.

### So have we just re-accreted in a new costume? No — and here's the test

Because length is explicitly fine, the accretion smell can't be "entries are long." It is **scatter and mystery**: behavior spread across a sprawl of context-named form siblings (`*_after_it_and_vie`) or into a third relational file, and lines whose reason the author has forgotten. The recast test for a healthy entry, however long: it is **locally complete** (everything the letter does is right there) *and* **every line is explainable** — the author can say in a sentence why each one exists, with no mystery entries and no warts. A long entry that passes both is honest irreducible complexity; a long entry that fails either is accretion. The whole rebuild is the bet that the same complexity, rehoused this way, reads as the former rather than the latter.

## Stylistic sets are dual-purpose, user-facing, and they enable joins that are off by default

A stylistic set is **two genuinely different things** that happen to share the OpenType `ssXX` mechanism (answer: both):

- a **taste knob** — a cosmetic alternate (the loopier ·May) with no bearing on joining; and
- a **capability unlock** — it enables joins or entry/exit combinations that are deliberately *off by default* because they're awkward-but-sometimes-wanted.

**Audience:** primarily **document authors** composing text in the font, though readers are welcome to use whatever sets exist. So stylistic sets are a shipped, user-facing feature — not merely the font author's private authoring tool (even though the author uses them that way too, e.g. to satisfy an otherwise-impossible pin).

**They widen what's declared-capable — so what's *allowed* is a matrix, not a fixed set.** This is *not* about un-forbidding: there's no default veto being lifted. A stylistic set simply **enables a join that is merely not allowed by default** — one no form explicitly declared (consistent with opt-in capability — nothing joins until something says it can). ·Tea does **not** join both *to* and *from* at the baseline by default — that would double the stroke back over the letter — but with a stylistic set enabled, it's allowed (and the Manual does exactly this, once). Consequences that ripple backward through this whole document:

- What a join is *allowed* to do is a function of *(left capability, right capability, **active stylistic sets**)* — every "capability" earlier in this doc is implicitly *"under the default configuration,"* and a stylistic set can add to the set of declared-capable joins. (Taste **vetoes** are a separate layer that sits on top of whatever is allowed; whether a stylistic set can also lift an actual veto is left open — the ·Tea case is capability, not a veto.)
- **Pins must carry the stylistic-set dimension.** A `data-expect` assertion can pin behavior *under ssNN*, and the earlier idea of a "negotiable pin satisfiable only by enabling a stylistic set" is grounded here.
- **Matching the Manual exactly requires reproducing its one stylistic-set use** — so the mandatory tier itself isn't purely default-configuration; the test harness must activate the right set at that spot.

## The condition vocabulary is closed

A join rule may predicate only on this fixed set of context-axes — believed complete after much fiddling, and **now deliberately closed**:

1. the neighbor's **family** (·It, ·Vie…)
2. the neighbor's **form/state** (see the dynamic note below)
3. the **attachment height** (baseline, x-height, y=6, top)
4. the **stroke orientation/quality** at the attachment (horizontal vs. vertical — the ·No case)
5. **word position** (initial, final, isolated)
6. **boundary tokens** (`space`, ZWNJ)
7. the **active stylistic set(s)**

Closing the vocabulary is a load-bearing decision, serving two goals at once: a small fixed set is **learnable** (readability), and it's what makes the **depth bound enforceable** — the language literally has no words to reach past the window. Adding an eighth axis is therefore a real, considered **language change**, never a casual escape hatch. This is the explicit guarantee against the predicate language re-accreting.

### Dynamic dependence is real, and it's exactly what the depth bound contains

Axis 2 — the neighbor's *form/state* — means its **resolved** form: the one it actually took in context, not merely the forms it *could* take. That is a **dynamic outcome**, the result of the neighbor's own resolution, and it is precisely the cascade that forces the "two of every letter" depth-2 doubling. So the spec *does* permit rules that depend on a resolved decision — necessarily, given cases like ·It·Vie "exiting at baseline only sometimes" — and the **depth bound is the thing that keeps that dynamic dependence from cascading without limit.**

**Ligatures fit here without adding an axis.** A precomposed ligature like ·Out+Tea (which doesn't work as separate glyphs) is a **value**, not a new axis: the resolved glyph identity changes from two glyphs to one compound glyph, and that compound is a first-class repertoire member with its own entry/exit and capabilities. Predicating on "my neighbor is ·Out+Tea" is just axis 1/2 over the resolved glyph. (**Parked sub-question:** whether ligature *formation itself* is modeled as an ordinary join outcome or as a distinct substitution mechanism.)

## Two missing pieces the layering must account for

- **Kerning** is a real dimension, treated in its own section below.
- **Set algebra is underused.** The YAML has set *union* (a context set can include other context sets) but no set *subtraction*. Much override complexity is really "this set, minus those" expressed the long way (or by minting a form). A policy language with first-class **union and subtraction** over named sets would absorb a lot of what currently forces new forms.

## Kerning: form-aware, yet a flat sidecar — reconciled by what it's keyed on

Kerning is **both** a global and a per-pair fact:

- **Global:** the entire Senior font looks better with *every* letter kerned one pixel tighter — a single baseline adjustment.
- **Per-pair:** as in most fonts, specific pairs need their own kerning on top.

It applies to joined and non-joined pairs alike. And it is **not a dumb static table** — it must be aware of *resolved forms*: ·No·Pea needs no special kerning, but ·No.alt·Pea only looks right two pixels tighter; ·No·Tea needs none, yet in ·No.alt·Tea.half·It the ·No.alt and ·Tea.half want to sit closer because the ·Tea "isn't anywhere near the baseline anymore."

These two facts seem to pull apart — form-aware reasoning wants the full machinery, but kerning currently lives in a **separate flat file**. The separation is **purely a tooling accommodation, not a model statement**: the author doesn't trust a dependency-free, vibe-coded JavaScript editor to safely modify a deeply-nested, well-commented YAML file, whereas a flat YAML file with `---`-separated entries is "boringly reliable" for such a tool. (This is the one sanctioned exception to two-place locality, from earlier.)

**The reconciliation — key kerning by *resolved-form pairs*.** A kerning table keyed by post-shaping glyph identities (e.g. `qsNo.alt qsTea.half`) is simultaneously **flat** (a plain two-glyph table a dumb web app can edit) *and* **context-aware** (because a resolved form already encodes the context that produced it). The ·No.alt·Tea.half·It case needs no mention of ·It: ·Tea has *already* resolved to `.half` because ·It follows, so the flat key `(qsNo.alt, qsTea.half)` captures it. The depth cascade is **paid upstream** in form selection; kerning merely reads the resolved pair and needs none of the rule machinery itself — only resolved-glyph keys. So kerning stays a flat, reliably-editable sidecar without becoming a dumb context-blind table.

**Working hypothesis:** global tightening plus a resolved-form-pair table suffices. If a real case ever needs context *beyond* the resolved pair (something that changes kerning without changing either resolved form), it would demand the richer machinery — flagged, but not expected.

## How the two letters negotiate a join

Both letters can hold opinions about the same join, so the model needs a negotiation rule. Confirmed:

- **Veto is unilateral.** Either letter can forbid a join, and the other gets no say. If ·Way says "never join ·Thaw," the join is dead. Suppression does not negotiate.
- **Making a join requires mutual capability.** A join happens only where the left offers an exit and the right accepts an entry that are compatible — same attachment height, within the deformation budget. Neither side can force a join the other can't physically accept.
- **Precedence among permitted options is genuinely case-by-case.** It is *not* a fixed "lead always wins." Sometimes the follower's preference should dominate; the author is confident research would surface clear follower-wins cases.
 So the model must not bake in lead-supremacy beyond a weak default.

The recent "let a form declare it wins word-final" mechanism is **totally a symptom** of a missing systematic precedence concept — an ad-hoc "I win" flag standing in for a principle the model should supply.

**Proposed precedence concept (to confirm next):** resolve conflicts by **rule specificity** — the more narrowly-conditioned rule wins, regardless of which side it sits on. This is side-agnostic, so it naturally produces follower-wins outcomes whenever the follower's rule is the more specific one, and it absorbs "wins word-final" automatically (a rule conditioned on word-final position is simply more specific than an unconditioned one). The weak **lead preference** survives only as a tie-breaker when two rules are equally specific. Open question: are there real conflicts where both rules are equally specific and the author must still name a winner by hand?

### When rules are incomparable (specific along different axes)

Specificity only gives a *total* order when conditions nest. Two rules can be **incomparable** — ·Way's conditioned on word position, ·Thaw's on the following letter — neither nesting inside the other. The decided handling:

- **Default: refuse to guess (option 1b).** An incomparable conflict is a **hard build error**. The author must record an explicit tie-break, which itself becomes a legible, more-specific rule the two letters can see. The build never resolves such a conflict silently. This squarely preserves the prime directive: a wrong outcome is caught by the machine, never left for the eye. A fixed axis-priority ordering (option 1c) is **rejected as the foundation** — the author is confident he'd never get such an ordering correct and complete.
- **Acknowledged risk:** 1b can breed a combinatoric pile of hand-recorded tie-breaks, and a long rule list is itself a readability tax — "there's just *so much there*." 1b without a release valve could re-accrete.
- **The release valve: named case-groups via set algebra.** The author already thinks in terms of **"ill-defined case groups"** — clusters of conflicts that should resolve the same way, but whose membership isn't yet crisply stated. The fix is to let the author *name* such a group (defining its membership with set **union and subtraction** over repertoire/context sets) and attach **one** resolution to the whole group — collapsing many individual tie-breaks into a single legible rule. This is the disciplined form of "sensible defaults," and it is **group-based, not axis-based**, precisely because a global axis ordering will never be complete. Like don't-care, these groups are **discovered incrementally**: start with explicit 1b tie-breaks, and promote a recurring pattern into a named group once it reveals itself. A small, fixed axis default (1c) may still be introduced later for a handful of truly universal cases, layered on top — never underneath.

## Trusting a change

This is the half that hurts most. After a change rebuilds the font, attention sorts into four strata:

1. **A pin broke** — build goes red, like a failing test. Not a punch-list item.
2. **A defect appeared** (collision, capability-mismatch) — auto-reported, must fix. Not a judgment call.
3. **A previously-pinned pair changed** — reviewed.
4. **A don't-care pair changed** — **surfaced by default**, and reviewed fast.

### The review workflow: fast, keyboard-driven, opinion-stamping

Stratum 4 is surfaced by default, not silenced — because silence forfeits the *discovery moment* where a don't-care pair reveals it should have been a do-care pair. The author has had real success with **keyboard-driven web apps that render lots of opinions quickly**, so the verification surface is one of these: every change is shown, and the author stamps each with an **opinion drawn from a small, moderately standardized vocabulary** of verdicts. Two requirements on that vocabulary:

- Opinions **copy-and-paste into punch lists** that drive the next round of agent edits.
- Opinions ultimately **become pinned assertions** — concretely, `data-expect` assertions that lock the behavior in. Generating the right assertion is easy in easy cases and genuinely painful in hairy ones; the tooling should carry as much of that as possible.

So the loop is: change → render everything → stamp opinions fast → opinions flow out as both a punch list (for fixing) and new pins (for locking).

#### This is a real application, not a textarea

The review/editing surface is a missing limb the rest of the design assumes but hasn't sized. It must let the author evaluate **moderately-large batches — up to hundreds of decisions (not thousands)** — on whether a change, a form, or a join is good. Two tiers of tooling, by data shape:

- **Flat data** (kerning) can be copied and pasted into a `<textarea>` and edited by a trivial, dependency-free web app. Boringly reliable.
- **Complicated nested structures** (the main spec) cannot be safely edited that way. For those, the author expects to write a **real program with its own web host that edits files on disk directly** — not copy-paste. This is the tool that makes hundreds-at-a-time review and editing tractable, and building it is part of the work, not an afterthought.

(This also softens the kerning "must be flat" claim: kerning is flat *given the textarea-grade tool*; a real on-disk editor could in principle handle richer structure. The flat sidecar is the pragmatic choice today, not an eternal law.)

### Pins assert minimal properties, never snapshots

A strong, decided preference: a pin should assert the **weakest property that captures the intent** — exactly the spirit of the existing `data-expect` assertions. **Brittle tests are the enemy.** The author does not think in terms of "blessing" an exact rendered result; he thinks in terms of *pinning behavior with the most minimal test that still catches the thing he cares about*. This is why "fine either way" is common and must stay cheap: an over-pinned snapshot would cry wolf on every acceptable variation.

### The combinatoric wall — and the separability that could break it

The unit being pinned is, in the hard cases, a **pair in context**, and that is where the cost explodes. To lock a pair ·X·Y *regardless of surroundings*, the current setup appears to require sweeping **two of every letter before and two of every letter after** (including `space` and the zero-width non-joiner) — on the order of **46⁴ combinations**, because a neighbor's own form can depend on *its* neighbor, so the influence cascades. The suite already runs 2–3 minutes pinning every core at 100%, and the lock-in is not yet complete.

The transformative win the author wants is to **provably shrink the test basis** — ideally to `46³ × 2` or fewer. The precise property that would buy this is **separability of left and right influence**: if a join's dependence on its left context is provably independent of its dependence on its right context, then sweeping the left fully (with a minimal right) and the right fully (with a minimal left) — "two-on-one-side-and-one-on-the-other, twice" — is *sufficient*, and the full left×right cross-product never needs to run. Establishing that bound (and the cascade depth behind the doubled neighbor) is a **core requirement**, not an optimization: it governs both testing cost *and* how far a rule's conditions are allowed to reach.

### Reality check: joint dependence is real, and depth is bounded by what you can afford

The clean theory (design-imposed separability, the cheap `46³×2` basis) is an **aspiration, not a guarantee.** The honest position:

- **Regime: aspire to A, fall back to B.** The author would *like* design-imposed locality (restrict the language so the cheap basis is sufficient by construction), but reaching it is "an involved factfinding mission" and what he wants **may simply not be possible.** The accepted fallback is to deal with it emotionally and **burn CPU on exhaustive testing.**
- **Genuine joint dependence exists — and it's the thing that breaks separability.** Worked case: in ·Utter·Gay·Low·It ("ugly"), the correct ·Gay form is the one that *both* gets joined-to by ·Utter at the x-height *and* joins to ·Low at the baseline. Each constraint is one-sided (entry from the left, exit to the right), but the **form is selected by priority over the *intersection*** of left-compatible and right-compatible forms — and the priority-winner of an intersection is **not** recoverable from each side's one-sided priority-winner. With a minimal right neighbor, the left sweep would pick a *different, higher-priority* ·Gay than the two-sided case demands. That is exactly why a context-specialized form like this ·Gay "might be pretty low down on a priority list": it only surfaces when both constraints bind at once. So naive left/right separability fails wherever contextual priority meets a two-sided constraint.
- **Cascade depth is affordability-bounded, not proven.** Two-on-each-side has empirically caught bugs that one-on-each-side missed; three-on-each-side is computationally intractable for even a *single* test (hours to days). So depth-2 is the **practical ceiling we can pay for**, and its sufficiency is *hoped*, not proven.

The implication for the rebuild: enforce a **depth bound by construction** — reject any rule that would reach deeper than the tested window, so depth-2 testing is provably complete *for the rules that exist* (a guarantee at the depth we can afford). Then **discover separability per region**: pay the cheap one-sided sweeps where left and right provably don't interact, and fall back to the full cross-product only at the genuinely joint-dependent spots like ·Gay-in-"ugly." Crucially, the build should **flag** which rules force the expensive path, so the cost is legible rather than mysterious.

### Whole-word assertions are the cheap, preferred lock where they fit

Many of the Manual's `data-expect` assertions are word-initial, word-final, or whole-word, and that is exactly what whole-word assertions are for. Their decisive advantage: **each costs one render — negligible CPU** — versus the combinatoric sweep a context-free pair lock needs. So whole-word assertions are the natural, cheap home for the mandatory tier and for any behavior expressible at word scope; the expensive pair-in-context sweep is the fallback only when word-scale can't capture the intent.

### The corpus is mostly generated nonsense

What gets rendered and diffed is, in the main, **generated nonsense** — synthetic letter sequences that exercise every pair (and deeper combinations). This *is* the separability sweep from above, wearing its other hat. The reason it must be synthetic rather than real text is decisive: **most of the constraints the author wants to correct don't show up in real words at all.** The Manual's text plus its `data-expect` attributes supply many must-have constraints, but they are a **minority** — there are far more constraints *outside* the Manual than in it. Real prose simply never visits most of the problem pairs.

Consequences that ripple through the verification story:

- The Manual corpus is the cheap, authoritative **must-have** subset; the generated-nonsense corpus is the **exhaustive** bulk that actually surfaces the bugs.
- "Surfaced by default" therefore shows mostly *synthetic* combinations — which is precisely why the review application must scale to **hundreds** of decisions at a sitting, and why the success metric (machine notices, human only judges) lives or dies on that tool.

## Selection is local and explainable — and the real activity is forbidding

**Selection regime: local and explainable, decisively.** At each position the system picks the highest-priority form whose two-sided constraints are satisfiable given its neighbors, and every choice is explainable in those terms. Global join-maximization is *theoretically* possible but **rejected**, for a grounding reason: a real Quikscript writer doesn't plan far ahead either, and won't restructure a whole word just to make it marginally faster to write. The font should mirror how a human actually writes — locally. So a locally-best choice that leaves a neighbor slightly worse is *accepted*; the system never reshuffles a word for global optimality.

**"More joins are better" is only a soft tiebreaker.** Among otherwise-equal options, prefer the one that joins — but the preference yields freely to taste. ·He·Owe is forbidden purely because, joined, it "is ugly and kind of awkward to write" and the author would never write it by hand, soft join-preference notwithstanding.

### Two levels: opt-in capability, then forbidding within it

An important correction to a tempting oversimplification. It is *not* "all forbidding," and the substrate is *not* blanket-permissive. There are **two distinct levels**, with opposite default polarities:

- **Capability is opt-in (explicit declaration).** By default, *no letter can join its neighbors anywhere* — a join is possible only where a form **explicitly declares** the capability. This is positive declaration, the opposite of forbidding: nothing joins until you say it can.
- **Within declared capability, forbidding is the labor.** *Among the pairs that genuinely can join,* the soft "more joins are better" pull applies, and the overwhelming bulk of day-to-day authoring is *negative space* — saying "not this one" to the declared-capable joins that turn out ugly or broken.

So "the project is forbidding" is true of the **selection/curation layer**, not the **capability layer**. Global optimization is out of scope at both levels; being told a better global assignment existed "might be nice" but the author doesn't expect to act on it.

This reframes the entire spec. Its quality is measured chiefly by **how cleanly it lets the author forbid** — at the pair, context, and group levels — without minting forms. Two kinds of forbiddance, with very different economics:

- **Broken** — collisions, reaching joins with no acceptor, height-mismatches. Objective. The machine *should find these for you*, so this labor shrinks toward zero as detection improves.
- **Ugly** — ·He·Owe and its kin. Taste — but, importantly, *not entirely* oracle-less (see below). The residue with no machine signature is irreducibly hand-authored by eye through the review workflow; no oracle but the author.

### Broken is an agent loop you are not in

For at least one workable definition of "broken," **all** broken joins are automatically detectable, at least in theory. The intended consequence is strong: broken joins should be **fixed by an agent running `make test` in a loop — possibly over and over — not by the human at all.** The human exits the broken-fixing loop entirely; the machine detects, fixes, re-tests, repeats until clean. (**Open task:** pin down the definition(s) of "broken" precisely enough that detection is provably complete — that definition is what the whole loop rests on.)

The balance of labor has also shifted: there *has been* a giant amount of ugly, but largely because the font wasn't fully specced. Now that a complete font exists, ugly is **bounded** — there "might only be a large amount" rather than an unbounded amount — while broken remains a major, ongoing share. So a strong defect detector plus the autonomous fix loop is genuinely high-leverage.

### What "broken" means — and the line against "ugly"

"Broken" is defined structurally, with no appeal to taste: **a join whose rendered geometry violates a structural invariant checkable from the bitmap plus anchor metadata.** The working (closed, "for future work") set of invariants:

- **Off-anchor contact** — ink touches or overlaps at a point that isn't the anchors.
- **A selected join that doesn't physically realize** — the sharpest case. If a form is chosen *because it claims to join* (e.g. ·Out's common x-height-connecting variant is selected), it is broken when, after all extensions and contractions are factored in, either (a) the next letter's ink **doesn't physically touch** where ·Out ends, or (b) the next letter was supposed to **switch to a touching bitmap and failed to.** A declared join must actually connect.
- **Height mismatch** — the two attachment heights don't meet.

Two refinements that define the system's posture:

- **Broken is default-rejected but appealable.** If a join the detector calls "broken" actually looks fine, it can be **declared OK** — an explicit, recorded exception. The detector is a default-reject with an override, not an absolute law.
- **The broken/ugly line is: structure vs. taste.** *Broken* asks "does it physically connect correctly?" *Ugly* asks "does it look and feel right?" — even when it connects. So **orientation-mismatch (the ·No horizontal-vs-vertical case) is *ugly*, not broken**: the machine only *flags* it; the author decides. Broken stays purely structural, which is exactly what lets its detection be complete and its fixing autonomous.

### Some "ugly" has machine signatures

Certain classes of ugliness carry detectable signatures and should be machine-flagged (and sometimes machine-fixed), not left to the eye:

- **Off-anchor contact** — two letters touching at a point that is *not* their anchor points. This is reliably a call to add an **extension of one or more pixels** to separate them or route the contact through a real anchor — "unless something unforeseen comes up." So it's auto-*proposable*, but the "unless unforeseen" is exactly where the tooling should **ask** rather than silently apply (see the deformation discussion).
- **Orientation mismatch** — some letters join best with **horizontal** strokes and are awkward with **vertical** ones; ·No is the classic. This generalizes the "two near-verticals read as one thick stroke" defect into a per-letter property.

The second case exposes a capability wrinkle: a form's join surface isn't only *where* it attaches (height) but *how* — the **stroke orientation/quality** it wants at an attachment. The capability model needs that dimension, because it's what lets the machine flag orientation-mismatch ugliness instead of leaving it to taste.

<!-- Interview in progress: more sections to come. -->
