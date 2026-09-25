# Core idea

This document states the requirements for a from-scratch rebuild of Abbots Morton Spaceport, a Quikscript Senior font generated from a YAML specification. It is being written from interviews with the author. It records _what the system is for_ and _what makes a design good or bad_. It does not say how to implement it; `doc/rebuild-design.md` does.

## Vocabulary

These words have fixed meanings in this document:

- **Rune**: the unit of the script that owns a set of stances. Every rune is either a **character** or a **ligature**, and either kind may be drawn with more than one motion. The note below explains the choice of word.
- **Character**: a rune _with_ a code point. It is one of the 44 Quikscript letters (·Pea, ·May, …): the abstract input unit, independent of how it is drawn.
- **Ligature**: a rune that joins a _sequence_ of characters into one drawn unit and has _no_ code point of its own, such as ·Out+Tea, which does not work as separate glyphs. Like a character, a ligature may have more than one stance.
- **Bitmap**: a grid of on/off pixels that a rune can be drawn as. A rune has one or more bitmaps. ·May needs at least two: one that a preceding rune can join at the baseline, and one that joins the next rune at the baseline.
- **Ink**: a filled bitmap pixel (a `#` cell). The font’s strokes are made of ink. Much of the join checking is about _where_ a stance’s ink falls. Off-anchor contact, for example, is ink touching where it should not.
- **Stance**: a bitmap together with its complete join policy: which entries it accepts, which exits it offers, which combinations of the two are legal, and which joins it refuses. A stance is _one real motion for writing the rune_ plus its join policy, not just a silhouette. A rune has one or more stances, and each stance compiles to one or more glyphs.
- **Glyph**: the output shape the shaper selects, in the OpenType sense. It is what a stance compiles to, and it usually has no code point of its own.
- **Anchor**: a named attachment point on a stance, in the OpenType sense. The **entry** and **exit** anchors drive _cursive attachment_ (`curs`): the shaper makes a join by placing one rune’s exit anchor on the next rune’s entry anchor. A stance’s join surface is the set of entry and exit anchors it carries and their heights.
- **Extension**: pixels added to a stance’s connecting stroke so a join physically connects. The two runes already meet at the same height, but one or both connecting strokes must be lengthened to touch. An extension is a parametric adjustment to a stance’s geometry, _not_ a stance of its own. Its per-instance value is the _extension amount_. “Extension” is the default name for the adjustment in either direction, because lengthening is by far the common case.
- **Contraction**: the same adjustment in the other direction, which _shortens_ the connecting ink. It is rare. When a statement must name both directions, this document writes _extension/contraction_; otherwise _extension_ includes contraction.

Why **stance**: “form” already has several meanings in type design (_letterforms_, _contextual forms_, _positional forms_), and a reader would bring the wrong one. “Stance” has no type-design meaning, and it suggests both things a stance is: a drawn posture (it _is_ a bitmap) and a fixed policy about joining (what it offers, accepts, and refuses).

Why **rune**: the things that own stances are characters _and_ ligatures, and “character-or-ligature” is clumsy. The nearby words are taken. A **glyph** is the compiled output shape. A **character** has a code point by definition, so a ligature is not one. _Letter_ and **form** carry type-design meanings. Every character here _is_ a Quikscript letter, but the document says “character” to keep the unit with a code point distinct from the glyphs that draw it. Unicode’s Runic block (U+16A0–16FF) holds real runes, but none appear in this Quikscript-only document.

## What we’re rebuilding, and why

The goal is the same as the current repository’s: a Quikscript Senior font whose rune shapes and, most importantly, whose **joins** (which pairs join, which don’t, and what each join looks like) are defined by a human-written YAML spec. A program reads the spec and produces a font that matches it.

What changes is the _kind_ of spec. The current system was built before its author understood the problem, so its rules were added one patch at a time. That is the main problem the rebuild must fix.

### The wound: accretion, and the verification slog it causes

The author’s diagnosis, in priority order:

1. **Rules added one patch at a time are the biggest problem.** Each new join problem got its own special-purpose mechanism: `strip_entry_before`, the pending-entry guard tables that `derive_pending_bk_entry_guards` and `derive_pending_fwd_strip_guards` build, `noentry_after` propagation, scoped-anchor selectors with `except`, post-liga cleanup passes, and more. There is no small set of independent concepts, only a large set of special cases. The current design may be a local maximum that is hard to leave.

2. **The day-to-day cost is verification.** The author is not afraid to change the system, partly because most (not all) of the domain can be tested automatically. The cost is time. After a change, the author must check by hand that nothing _else_ broke, and must build a to-do list that marks each affected pair “this is wrong”, “this is right”, or “this is fine either way”.

3. **Unpredictability matters somewhat.** A small source change, such as a widened selector, can affect other pairs. This is secondary to the first two problems.

So the rebuild is mainly about the cost of _trusting_ a change, not the difficulty of _making_ one. A good design makes the effects of an edit cheap to see and cheap to verify.

## The load-bearing column: kill the whack-a-mole

One outcome would make the rewrite a failure: **the finished font is still about as unreliable, and still needs a lot of manual searching to _notice_ ugly joins and forbid them.** Most of the cost is in the **noticing**. Forbidding an ugly join once it is found is cheap. _Searching_ the corpus for it is slow.

So the success metric is: **the machine does the noticing, and the human only judges what is shown to them.** The defect detector finds every broken join, and the review surface shows every relevant change. The author should never have to search for something that got worse without notice. Every other decision in this document serves this goal. A choice that does not reduce manual noticing is not justified.

## The OpenType ceiling is a hard wall

The product is **a real font that people can use on today’s computers**, with the rendering software they actually have. So **OpenType is a permanent constraint.** If OpenType can’t shape something, the author can’t have it, and it **must not be expressible in the spec.** Every capability, join, veto, extension, stylistic set, and pin in this document is limited to what OpenType (GSUB and GPOS: `calt`, `liga`, `ssXX`, anchors) can produce. Most of what the author wants has turned out to be possible within those limits.

## Greenfield encoding, sacred cargo

“Recreate it totally differently” means the **encoding** is replaced: the YAML structure, the patch stances and override lists, and the Python/FEA pipeline. The _content and the concepts_ carry over mostly unchanged:

- **Anchor points**, which OpenType’s cursive attachment (`curs`) uses to join glyphs.
- **The Manual corpus and its `data-expect` notation** for stating which joins must happen at which heights.
- **The concepts of attachment height and anchor.**
- **Nearly all the drawn bitmaps.** A few more may be drawn. **No bitmap is generated algorithmically from another bitmap.** The base bitmaps are all drawn by hand, and extension adjusts geometry on top of them without deriving one base shape from another.
- **All the ductus information** (see below).
- **The set of characters**, which is complete for Quikscript.

### The stance set’s truth is held jointly by ductus _and_ bitmaps — and finishing the ductus gates the rewrite

The stances-first model depends on each rune’s full set of motions. **Two equal sources** define that set, and neither contains the other:

- the **ductus**, which lists _how_ the rune is drawn (the strokes, their order, and the number of distinct motions), and
- the **bitmaps**, the pixels that realize those motions. They are authoritative on their own, and none is derived from another.

The ductus is incomplete, and the author wants to **finish writing all of it before starting the rewrite**. The failure this prevents: you write down four of a glyph’s motions and forget the fifth. A stance set can be called “closed” (the property the authoring model depends on) only if the ductus that lists it is complete. So **completing the ductus is a precondition** for the rebuild. The bitmaps are equally authoritative, but the ductus is the list that shows the bitmap set is complete and not missing a fifth stance.

## The deepest principle: discovery, not declaration

This principle underlies all the specifics. **Much of this project is _discovering_ what looks good, what looks bad, and which rules produce good results.** The author is not transcribing a finished design. So the spec must never require a boundary to be drawn correctly _in advance_. Each important classification is **discovered over time and changed in place**, and the tooling should make that change easy:

- **don’t-care → do-care:** when the author looks at a pair he never considered, he finds that he cares about it.
- **one-off tiebreak → named case-group:** a conflict resolved by hand recurs, and the author turns the pattern into one named rule.
- **ugly-with-a-signature → broken invariant:** a recurring ugliness turns out to have a structural sign, and the author adds it to the machine-checked set.
- **broken → declared-OK:** a join the detector rejects looks fine, and the author records the exception.

This is a confirmed design decision, not a side effect of an unfinished font. The system is a tool for _finding_ the right rules. Its defaults are permissive _where that is safe_ (the selection level: don’t-care, more joins are better) and opt-in where they must be (the capability level: nothing joins until a stance declares that it can). Its boundaries can move, and “I couldn’t have known that in advance” is a supported workflow. Read every “default”, “veto”, “pin”, and “forbid” below with this in mind.

## Where the authority for “correct” lives

No single source decides correctness. Correctness has **tiers**, and each tier has a different source. This matters because the verification cost comes from treating all joins the same.

1. **Mandatory joins: the canonical document.** The Quikscript Manual marks near-mandatory joins with `data-expect` attributes. A core goal of this font is to show that OpenType tooling can produce a font that joins **exactly as the Manual does**. For these pairs, correctness is objective and external: the font is right if and only if it matches the Manual. This tier should be checked by machine against the Manual corpus.

   **Caveat: the Manual is not self-consistent.** No single consistent rule set reproduces it. The Manual writes some words one way and other words another way, and a single font can match **both** only with **stylistic sets**: some words are written with a set enabled and others without. So the mandatory tier depends on **configuration**. “Matches the Manual” means “matches it under the stylistic-set configuration the Manual uses at that spot”, not under one global default. So stylistic sets are required even for the mandatory tier, not only for taste.

2. **Objective defects: joins that are broken.** Even within the permitted rules, shaping can produce results that are plainly wrong, for example:
   - Two near-vertical runes set immediately adjacent, so the join reads as one thick stroke instead of two runes.
   - A rune drawn in a stance _shaped to join_ its neighbor at a particular height, when the neighbor can’t accept a join at that height. The join reaches for an attachment that does not exist.
   These are not matters of taste. A good design makes them structurally impossible or detects them automatically. The author should never have to find them by eye.

3. **Discretionary joins: taste.** Many joins are _permitted_ by the rules, but the author **disallows** them because they would be awkward to write by hand or look ugly. The author’s judgment decides these. The to-do-list verdicts map onto the tiers: “wrong” is a violated mandatory join or an objective defect; “right” and “fine either way” are discretionary calls that match taste or are acceptable.

The “fine either way” verdict matters: for some pairs, _more than one_ outcome is acceptable. A spec that pins every pair to one exact result would report false failures on acceptable changes. A spec that pins nothing misses real regressions. The rebuild must let the author say how tightly each pair is _pinned_ and how much it is left _free_.

## Don’t-care is the default — and it’s discovered, not declared

In ordinary text, most joins are don’t-care. A sequence of four or five characters might have one or two firm requirements from the Manual, and the author does not want to specify the rest. One global preference applies to the don’t-care space: **all else equal, more joins are better than fewer.**

The author **cannot reliably classify** a pair as don’t-care or do-care in advance. A pair stays don’t-care until the author looks at a result and decides that he cares. So the spec must not require an up-front verdict for every pair. It must support **incremental pinning**: start permissive, and let the author mark a pair as cared-about when he notices something, recording the verdict then.

This changes the to-do list. Only a few kinds of change should need human attention:

- **Pinned (mandatory) breakage**: never on the to-do list. Automated tests catch and reject it. A pin can occasionally be relaxed, for example when it can be satisfied only by enabling an otherwise unwanted stylistic set for part of it. The author relaxes a pin rarely, and only after an agent has tried at length and failed. It is never the default.
- **Objective defects**: never on the _human’s_ to-do list. They are detected automatically so an agent can fix them. They take more of the author’s time than anything else (see below).
- **A previously blessed discretionary pair that changed**: this is the only legitimate item on the to-do list.

A changed don’t-care pair is not an error. It is shown for a quick review (see “Trusting a change”). The author acts only on pairs he already cares about and on defects the machine finds.

## The real job: cleanly express what each rune may and may not do

The author’s main complaint about the current design is that it **does not clearly express what a given rune may and may not do.** Most of the debugging of constrained-but-free cases happens here, and fixing it is the rebuild’s central task.

A rune’s join behavior depends largely on **how the rune is physically written**, not on taste. These are structural capabilities. For example:

- A rune can be _joined_ at the x-height and _exit_ at the baseline, **or** the reverse (entry at the baseline, exit at the x-height). It **cannot** enter and exit both at the x-height, **or** both at the baseline, _unless a particular stylistic set is enabled_ to allow that combination.

So each rune, and each stance, has a **join surface**: which entry heights it accepts, which exit heights it offers, and which _combinations_ of the two are legal at once. Stylistic sets can allow combinations that are otherwise not allowed. If the join surface is modeled clearly and accurately, two things follow. The shaper can never select a stance that reaches for an attachment its neighbor can’t provide, so that class of defect becomes impossible by construction. And the free choices are clearly separated from the combinations that are not allowed.

## Contextual preference is a first-class, common pattern

A common case of a constrained but free choice: a stance is **preferred in isolation**, but should give way when another stance improves the surrounding joins. Example: `·It·No.alt` is preferable to plain `·It·No` on its own. **But** if plain `·It·No` improves the wider context, by allowing a baseline join into ·It before it or an x-height join out of ·No after it, then plain `·It·No` should win. The spec must be able to say “prefer X in isolation, but switch to Y when Y gives a better neighbor join”, without special-purpose machinery for each case.

## Defects must be found by the machine, not the eye

Defective pairs take **most** of the author’s debugging time. Detecting them automatically, so an agent or the author can fix them, is a top-priority requirement, about as important as readability. The two named kinds of defect so far:

- **Collision / false stroke:** near-vertical runes set adjacent, so the pair reads as one thick stroke.
- **Reaching join with no acceptor:** a stance shaped to join at a height the neighbor can’t accept.

Both can be derived from accurate capability and geometry data, and neither should require the author to see it.

## The unit of authoring is the written stance (stances-first)

The spec is **stances-first**, not capability-matrix-first. A rune is written as a small, **closed, explicitly declared set of stances**: the real motions a hand would draw it with. ·May, for example, can be written counterclockwise or clockwise, and each motion needs its own bitmap to look right. The author wants to state that ·May has _only these N motions_ for being written and joined, and no more.

Two properties are required, and they pull against each other:

- The stance set is **finite and named**: you can read a rune’s complete set of stances and know there are no others.
- The stance set **changes**. As the font is refined to match what a real Quikscript writer would do, stances are added, refined, or removed. “Closed” means _fully listed now_, not _fixed forever_.

The legal join surface (which entry and exit heights, and which combinations, a rune supports) is **derived** from the stance set and shown to the author, not declared separately. The readability of a heights-first view is kept as a _derived display_, while authoring stays based on real written stances.

**Open question:** stances-first _is_ essentially the current model, and the current problem is stances added one patch at a time. So the rebuild depends on a principled answer to this question: _what makes a stance a legitimate member of the stance set (a real motion for writing the rune), and what makes it a patch (a stance that exists only to fix one join bug)?_ Without that rule, a stances-first design could lead back to the same local maximum.

## Attachment heights

The attachment heights are:

- **baseline**
- **x-height**
- **y=6**, used only for the ·See·Pea and ·Pea·Pea joins (currently, and likely always)
- **top**, for example the ·See·Tea join

## Reaching often requires extension — and extension must be controlled

Matching attachment _heights_ is necessary but not sufficient. To reach an awkward attachment point, a stance’s connecting stroke sometimes has to be **extended** (or, less often, **contracted**). This is a separate capability from which heights a stance offers.

This is also a current source of bugs: an LLM does not reliably extend _exactly_ the strokes that should extend and leave the others alone. The author’s preferred fix is in the workflow as well as the data: the tooling should **ask the author** when an extension or contraction is in question, and not guess. (This applies the project rule to ask when there are several valid choices.) Whether an extension is a stance or a separate adjustment is settled below.

## What a stance is — and the category error behind the accretion

The author’s definition: **a stance belongs in a rune’s stance list if and only if it specifies a bitmap together with everything that is possible with it and how it should join to other things.** A stance fully describes one real motion for writing the rune and all its join capability.

The patch stances contradict that definition. Stance lists grow because adding a stance is the easiest way in the current YAML to express something that is not a _motion for writing the rune_. It is a **contextual join override**, most often a _suppression_: “in this case, don’t join in _this_ way, even though it would otherwise be allowed.” The stance exists only to hold that override. Such stances are named after the _context that caused them_ (`*.before-day-exam`, `*_after_it_and_vie`, `*.ex-noentry`), not after a motion for writing the rune, and the name reliably shows it.

The override complexity is real, and the author believes it is **irreducible**: the domain and OpenType’s limits are that complex. The goal is to **put it in the right place**, not to remove it. The author moves as much logic as possible into the Python. What can’t go there has become long override lists in `quikscript.yaml` that “smell like warts.”

### Where the pressure goes when stances stop carrying it

Split what a patch stance does into its parts, each with its proper place:

- **A different written shape** stays a stance, because it has its own bitmap and the definition above requires it to be a stance. But its _triggering context_ must not be part of its identity or name.
- **The same shape, extended to reach** an awkward attachment, is an **extension/contraction**, not a new stance.
- **The binding of a stance to a context** (“when does this stance apply”) and **pure suppression** (“don’t join ·X·Y this way, or at all”, with no shape change) are relational join rules **written on the runes themselves** (see “Locality of reference” below), over the clean stance set. They are not new stances.

**Example: `qsNo.stances.alt_after_it_and_vie`.** It specializes `qsNo.stances.alt` by inheritance, and inheritance stays in the rebuild. It is _not_ a tighter shape. It exists because **·It and ·Vie connect at the baseline only _sometimes_**: the predecessor’s baseline exit is _conditional_, and ·No must select a matching stance when, and only when, that exit is present. So its content is **selection conditioned on the neighbor’s state**, not a new motion for writing ·No. It is a separate stance only because a new stance was the least bad place for that condition in the current YAML. The rebuild must let this condition be attached to an _existing_ stance, instead of adding a sibling named after the neighbors that trigger it.

## Locality of reference: at most two places

This is a decided, hard constraint. To understand whether and how a pair of runes joins, a reader looks in **at most two places: the left rune (its bitmaps and stances) and the right rune (its bitmaps and stances).** There is **no separate relational file**, because a third place to look breaks locality.

Consequences:

- **Suppression is written on one of the two runes.** “·Way·Thaw must never join” is written on ·Way or on ·Thaw, with a **weak preference for the left rune (·Way)**. That matches how the author thinks, and OpenType also processes text left to right by default.
- A relational rule is a rule a _rune_ owns about its neighbors, not a separate pair object. The set algebra and the conditional-selection language exist so that an _existing_ stance, or the family, can hold these rules, and contextual overrides stop requiring new stances.
- **One allowed exception:** kerning may live in a separate file if that allows better tooling, such as pasting kerning data into a `<textarea>` and editing it in a small web app. Easy bulk editing can outweigh strict locality for kerning only. Everything else stays in the two runes.

## Extension is a parametric adjustment, authored, owned by a stance

For joins, an extension is a **parametric adjustment, not a stance.** ·Jay has _one_ exit. “Extend by 1 toward ·Exam” is a small directive, shaped like the current `extend_exit_before` and `contract_entry_after`, that the build applies to generate the geometry. This keeps the stance set small: there is no `·Jay-exit-extended-1px` stance. Extensions that are not for joins may behave differently, and that case is open.

- **Trigger: declared by the author by default.** Extensions and contractions are _declared_. Some could be triggered by a detected need, but the **extension amount** (1px or 2px) is an aesthetic judgment, and the author decides it. The machine may _propose_ an amount.
- **Placement: on a stance, preferably the left one.** Extensions are written on one or more stances, so locality holds: they are on one of the two runes. The author prefers the **left** rune, but the **right** rune is sometimes clearer, and one extension may be **split** across both sides, partly or fully.

### A mis-scoped extension can be a symptom of an under-fleshed stance set

A recurring bug: an extension directive applies to **the wrong set of motions for writing a rune**. It extends the intended stroke _and also_ one that should not change. There are two different causes, and it matters which one applies:

- **Wrong scope**: an author or LLM error in the current YAML. The directive’s target set is wrong, and the fix is to narrow it.
- **Too few stances**: two different motions for drawing the rune are still combined in one stance, so the directive has no precise target. The fix is to **split the stance** into the motions it needs, then aim the directive at the right one.

This is the ductus precondition again. When the stance set does not distinguish enough motions, extension directives have no precise target and spill onto sibling stances. A complete ductus gives every directive an exact target, so an extension cannot accidentally apply to a second stroke.

### The extension amount has a tolerance band, and “don’t join” is a real outcome

An extension amount is **too short** (a _hard_ error: usually still an off-anchor touch, so still broken), **OK** (a _range_ of values), or **longer than needed** (a _lesser_ error: tolerable but flagged). Sometimes **no** extension amount looks right, and the correct fix is **not to join**, which is a suppression. So an off-anchor contact is fixed by one decision: _put the extension in the OK range, or drop the join._

This reconciles asking the author with an automated loop. The loop can stop at **OK**, or tolerably at _longer than needed_, but never at _too short_. The range lets it reach a stable, shippable state without the author choosing exact pixels. The workflow the author wants:

- **Extension amounts are chosen by a fully automated loop** that aims for the OK range. It acts first and is reviewed afterward. It does not stop to ask during the loop.
- **A review surface shows every resulting change** for a quick **approve or reject**. The loop runs unattended, and the author reviews _every_ change it makes afterward.

So the review workflow’s verdicts need at least approve or reject for each change shown, plus “don’t join” as a verdict when no extension amount works.

## The readability bar: local completeness, even if the length is crazy-long

The decided definition of clear, easy-to-understand YAML is **local completeness, not minimal size.** Reading one rune’s entry from top to bottom tells you everything the rune does and every join it permits or forbids. Understanding a _pair_ may require **both** runes open at once: one rune per editor pane, two panes. That is two-place locality in practice.

Two admissions:

- What this font tries to do has a **large amount of irreducible complexity**, even before OpenType adds more, because its limitations must be worked around at every step. The rebuild does **not** promise short or simple YAML.
- **Long single-rune entries are accepted**, reluctantly, as the least bad option. A long entry is **not** evidence of a design failure.

### So have we just re-accreted in a new costume? No — and here’s the test

Because length is acceptable, the warning sign is not long entries. It is **scatter and mystery**: behavior spread across many context-named sibling stances (`*_after_it_and_vie`) or into a third relational file, and lines whose reason the author has forgotten. A healthy entry, however long, passes two tests: it is **locally complete** (everything the rune does is in it), _and_ **every line is explainable** (the author can say in a sentence why each line exists). A long entry that passes both holds irreducible complexity. A long entry that fails either is made of patches. The rebuild assumes that the same complexity, organized this way, passes both tests.

## Stylistic sets are dual-purpose, user-facing, and they enable joins that are off by default

A stylistic set serves **two different purposes** through the same OpenType `ssXX` mechanism:

- a **cosmetic alternate**, such as the gapped ·Owe of `ss06`, that does not affect joining; and
- a **capability unlock** that enables joins or entry/exit combinations that are _off by default_ because they are awkward but sometimes wanted.

**Audience:** mainly **document authors** writing text in the font, though readers can use any set that exists. So stylistic sets are a shipped, user-facing feature. The font author also uses them while authoring, for example to satisfy an otherwise impossible pin.

**They add to what is declared capable, so what is _allowed_ depends on the configuration.** A stylistic set does not lift a default veto. It **enables a join that no stance declared by default**, consistent with opt-in capability: nothing joins until something says it can. By default ·Tea does **not** both enter and exit at the baseline, because that would double the stroke back over the rune. With a stylistic set enabled it may, and the Manual does this once. Consequences for the rest of this document:

- What a join may do is a function of _(left capability, right capability, **active stylistic sets**)_. Every capability earlier in this document is _for the default configuration_, and a stylistic set can add joins to it. Taste **vetoes** are a separate layer on top of what is allowed. Whether a stylistic set can also lift a veto is open; the ·Tea case is a capability, not a veto.
- **Pins must include the stylistic-set dimension.** A `data-expect` assertion can pin behavior _under a given ssNN_. This is where the earlier idea of a pin that can be satisfied only by enabling a stylistic set comes from.
- **Matching the Manual exactly requires reproducing its stylistic-set uses**, the `data-stylistic-set` attributes in `site/the-manual.html`. So the mandatory tier is not purely the default configuration, and the test harness must enable the right set at each of those spots.

## The condition vocabulary is closed

A join rule may depend only on this fixed set of context axes. The author believes the list is complete after much experimentation, and it is **closed**:

1. the neighbor’s **family** (·It, ·Vie, …)
2. the neighbor’s **stance** (see the note on dynamic dependence below)
3. the **attachment height** (baseline, x-height, y=6, top)
4. the **stroke orientation** at the attachment (horizontal or vertical; the ·No case)
5. **word position** (initial, final, isolated)
6. **boundary tokens** (`space`, ZWNJ)
7. the **active stylistic sets**

Closing the list serves two goals. A small fixed set is **learnable**, which helps readability. And it makes the **depth bound enforceable**: the language has no way to refer to anything beyond the window. Adding an eighth axis is a considered **language change**, not a quick workaround. This keeps the condition language from growing one patch at a time.

### Dynamic dependence is real, and it’s exactly what the depth bound contains

Axis 2, the neighbor’s stance, means its **resolved** stance: the one it takes in context, not the stances it _could_ take. That is a **dynamic result** of the neighbor’s own resolution, and this chain of dependence is why testing needs two neighbors on each side (depth 2). So the spec _does_ allow rules that depend on a resolved decision. It must, given cases like ·It and ·Vie exiting at the baseline only sometimes. The **depth bound is what stops that dependence from chaining without limit.**

**Ligatures fit without a new axis.** A ligature such as ·Out+Tea (which does not work as separate glyphs) is a **value**, not an axis. The resolved glyph changes from two glyphs to one ligature glyph, and the **ligature** is a rune with its own stances, entries, exits, and capabilities. A condition “my neighbor is ·Out+Tea” uses axes 1 and 2 over the resolved glyph. (**Open question:** whether forming a ligature is modeled as an ordinary join outcome or as a separate substitution mechanism.)

## Two missing pieces the layering must account for

- **Kerning** is a real dimension, covered in its own section below.
- **Set algebra is underused.** The current YAML has set _union_ (a context set can include other context sets), but _subtraction_ only in an anchor selector’s `except:` list, which drops families from the selected set. A named set cannot be subtracted from another. Much override complexity is really “this set, minus those”, written out in full or expressed by adding a stance. A policy language with **union and subtraction** over named sets would replace much of what currently requires new stances.

## Kerning: stance-aware, yet a flat sidecar — reconciled by what it’s keyed on

Kerning is **both** a global and a per-pair fact:

- **Global:** the Senior font looks better with _every_ rune kerned one pixel tighter, as one uniform adjustment.
- **Per-pair:** as in most fonts, some pairs need their own kerning on top.

It applies to joined and unjoined pairs. It must also be aware of _resolved stances_, so it is not a static table. ·No·Pea needs no special kerning, but ·No.alt·Pea only looks right two pixels tighter. ·No·Tea needs none, but in ·No.alt·Tea.half·It the ·No.alt and ·Tea.half should sit closer because the ·Tea “isn’t anywhere near the baseline anymore.”

These two facts seem to conflict. Stance-aware kerning seems to need the full rule machinery, but kerning is in a **separate flat file** (`glyph_data/senior_quikscript_kerning.yaml`). The separation is **only a tooling accommodation**, not part of the model. The author doesn’t trust a dependency-free, “vibe-coded” JavaScript editor to modify a deeply nested, well-commented YAML file safely, but a flat YAML file with `---`-separated entries is “boringly reliable” for such a tool. (This is the one allowed exception to two-place locality, described above.)

**This is the same tooling choice as for the review surface (see “This is a real application”).** There are two kinds of editor: a simple `<textarea>` copy-and-paste tool, which is safe only for flat data, and a **full web app with its own server that edits the nested YAML source on disk directly.** If that app exists, the _tooling_ reason for keeping kerning in a separate flat file is weaker, because the app could safely edit nested source. The separate flat file suits the current simple tool. It says nothing about the kerning model.

**The reconciliation: key kerning by _resolved-stance pairs_.** This follows from how OpenType is staged: _all_ substitution (GSUB: `calt`, `liga`, `ssXX`, and the extension substitutions) finishes before _any_ positioning (GPOS: cursive attachment and `kern`) begins. So when kerning runs, every rune has resolved to its final glyph, and the kern lookup sees only that final sequence. A table keyed by post-shaping glyph identities (for example `qsNo.alt qsTea.half`) is therefore both **flat** (a plain two-glyph table that a simple web app can edit) _and_ **context-aware** (a resolved stance already encodes the context that selected it). Two views of the same point:

- **General:** in **·A·B·C where ·C changes the shape of ·B**, ·B resolves to ·B′ during GSUB, so the kern stage reads the pair `(qsA, qsB′)`. The special kern is keyed on that pair, and kerning never needs to know about ·C.
- **Concrete:** in **·No.alt·Tea.half·It**, ·Tea has _already_ resolved to `.half` because ·It follows, so the flat key `(qsNo.alt, qsTea.half)` holds the tighter spacing without mentioning ·It. (·It is ·C, ·Tea is ·B, and `.half` is the changed shape.)

Extensions work the same way. OpenType can’t stretch a bitmap, so an extended stroke _is_ a separate glyph selected by GSUB, and it is already part of the key. Context dependence is **handled earlier**, in stance selection. Kerning only reads the resolved pair and needs none of the rule machinery, only resolved-glyph keys. So kerning can stay in a flat, reliably editable file and still depend on context.

**How complete is this?** A flat table keyed by resolved-stance pairs _is_ ordinary GPOS pair kerning. It covers every case that appears as a difference in resolved glyphs, and because GSUB runs first, that includes every contextual effect that changes a rune’s shape. It can’t express a kern that must differ while _both_ resolved glyphs have the same glyph identity[^glyph-identity]: a third rune that changes the wanted ·A·B spacing without changing ·A’s or ·B’s glyph. OpenType’s **contextual** kerning (a kern lookup that also inspects a resolved neighbor) exists for this case, so the fallback is a standard feature and needs no new machinery. Whether such a case occurs is open; global tightening plus the flat pair table is expected to be enough. A corpus check that flags any resolved-stance pair that wants two _different_ kerns would find a counterexample automatically.

[^glyph-identity]: A glyph’s identity in OpenType is its **glyph index** (glyph ID): an integer from 0 to one less than the glyph count in the `maxp` table, with index 0 reserved for `.notdef`. The index is the glyph’s position in the font’s glyph order: the order of `glyf`/`loca` records for TrueType outlines, or of charstrings in the `CFF`/`CFF2` table. It is _not_ computed from the outline or bitmap, so two glyphs with identical pixels in different slots are different identities. Code points appear only in the `cmap` table, which maps each Unicode code point to a glyph index. After that, GSUB and GPOS see only glyph indices. A pair-kern lookup (PairPos, GPOS lookup type 2) matches glyph IDs, either directly through a Coverage table and pair sets (format 1) or through ClassDef tables that group glyph IDs into classes (format 2). It never looks at pixels. The readable names this document uses (`qsNo.alt`, `qsTea.half`) are stored in the `post` table (or the CFF charset) for tools and debugging, and play no part in matching.

## How the two runes negotiate a join

Both sides have rules about the same join: what each side offers, accepts, or refuses there. The model must reconcile them. These rules belong to the **stances, the individual motions for writing a rune**, not to the rune as a whole. In particular, **no rune ever _requires_ a join**. Only a specific stance may carry a requirement, for a motion that only makes sense when joined. So “the two runes negotiate” means “their selected stances negotiate.” Confirmed rules:

- **A veto is one-sided.** Either rune can forbid a join, and the other has no say. If ·Way says “never join ·Thaw”, the join does not happen. Suppression is not negotiated.
- **A join requires both sides to be capable.** A join happens only where the left offers an exit and the right accepts an entry that are compatible: the same attachment height, and close enough that any needed extension or contraction can connect them. Neither side can force a join the other can’t physically accept.
- **Precedence among permitted options is decided case by case.** It is _not_ a fixed “left rune always wins”. Sometimes the follower’s preference should win, and the author is confident that research would find clear cases of this. So the model must not favor the left rune beyond a weak default.

An example from the old font: when two stances competed for selection, the winner was the one whose glyph _name_ sorted first, which is an accident of naming, not a decision. A change let a stance instead **declare** that it wins in word-final position. That declaration is **a symptom** of a missing systematic precedence concept: an ad hoc, per-stance tiebreak (replacing a name-sort order that is no principle at all) stands in for a rule the model should provide.

**A candidate precedence concept, not yet decided:** resolve conflicts by **rule specificity**, so that the more narrowly conditioned rule wins, whichever side it is on. Because this ignores sides, the follower wins whenever its rule is more specific, and it covers “wins word-final” automatically: a rule conditioned on word-final position is more specific than an unconditioned one. The weak **left-rune preference** is used only as a tiebreaker when two rules are equally specific. Open question: are there real conflicts where both rules are equally specific and the author must still choose the winner by hand?

### When rules are incomparable (specific along different axes)

Specificity gives a _total_ order only when conditions nest. Two rules can be **incomparable**: ·Way’s rule conditioned on word position and ·Thaw’s on the following rune, with neither contained in the other. The decided handling:

- **Default: don’t guess.** An incomparable conflict is a **hard build error**. The author must record an explicit tiebreak, which becomes a readable, more specific rule on the two runes. The build never resolves such a conflict silently, so a wrong outcome is caught by the machine and not left for the author to see. A fixed axis-priority ordering (a global ranking of which condition axes outrank which) is **rejected as the foundation**, because the author is confident he would never get such an ordering correct and complete.
- **Known risk:** refusing to guess can produce a large number of hand-recorded tiebreaks, and a long rule list is itself hard to read (“there’s just _so much there_”). Without a way to group them, the tiebreaks could become another set of special cases added one patch at a time.
- **The remedy: named case-groups using set algebra.** The author thinks in terms of **“ill-defined case groups”**: clusters of conflicts that should resolve the same way but whose membership isn’t yet stated precisely. The author can _name_ such a group, define its membership with set **union and subtraction** over stance and context sets, and attach **one** resolution to the group. This replaces many individual tiebreaks with one readable rule. It provides sensible defaults, and it is **group-based, not axis-based**, because a global axis ordering will never be complete. Like don’t-care, these groups are **discovered incrementally**: start with explicit hand-recorded tiebreaks, and turn a recurring pattern into a named group once it appears. A small, fixed axis-priority default may be added later for a few universal cases, on top of the groups, never beneath them.

## Trusting a change

This is the hardest part. After a change rebuilds the font, the results fall into four levels:

1. **A pin broke.** The build fails, like a failing test. Not a to-do item.
2. **A defect appeared** (collision, capability mismatch). Reported automatically, and must be fixed. Not a judgment call.
3. **A previously pinned pair changed.** Reviewed.
4. **A don’t-care pair changed.** **Shown by default**, and reviewed quickly.

### The review workflow: fast, keyboard-driven, opinion-stamping

Level 4 is shown by default, not hidden, because hiding it would lose the _moment of discovery_ when a don’t-care pair turns out to matter. The author has had good results with **keyboard-driven web apps that record many opinions quickly**, so the review surface is one: every change is shown, and the author marks each with a **verdict from a small, mostly standard vocabulary**. The vocabulary has two requirements:

- Verdicts **can be copied into to-do lists** that drive the next round of agent edits.
- Verdicts eventually **become pinned assertions**, concretely `data-expect` assertions that fix the behavior. Generating the right assertion is easy in simple cases and hard in complicated ones, and the tooling should do as much of it as possible.

So the loop is: change, render everything, record verdicts quickly, and turn the verdicts into a to-do list (for fixing) and new pins (for locking behavior in).

#### This is a real application, not a textarea

The rest of the design depends on the review and editing surface. It must let the author judge **moderately large batches, up to hundreds of decisions (not thousands)**, on whether a change, a stance, or a join is good. Two kinds of tooling, by data shape:

- **Flat data** (kerning) can be copied into a `<textarea>` and edited by a simple, dependency-free web app. This is “boringly reliable.”
- **Nested structures** (the main spec) can’t be edited safely that way. For those, the author expects to write a **real program with its own web server that edits files on disk directly**. This tool makes reviewing and editing hundreds of items at a time practical, and building it is part of the work.

### Pins assert minimal properties, never snapshots

A decided preference: a pin asserts the **weakest property that captures the intent**, as the existing `data-expect` assertions do. **Brittle tests are to be avoided.** The author does not think of “blessing” an exact rendered result. He thinks of _pinning behavior with the smallest test that still catches what he cares about_. This is why “fine either way” is common and must stay cheap: a snapshot pin would report a failure on every acceptable variation.

### The combinatoric wall — and the separability that could break it

In the hard cases, the thing being pinned is a **pair in context**, and that is where the cost grows fastest. To lock a pair ·X·Y _regardless of its surroundings_, the current setup appears to need a sweep of **two of every symbol before and two after**. The sweep alphabet is the 44 letters plus `space` and the zero-width non-joiner (ZWNJ), 46 symbols, so the sweep is on the order of **46⁴ combinations**. The reason is that a neighbor’s stance can depend on _its_ neighbor, so influence passes along the chain.

The author wants to **provably shrink the test basis**, ideally to `46³ × 2` combinations or fewer. The property that would allow this is **separability of left and right influence**. If a join’s dependence on its left context is provably independent of its dependence on its right context, then sweeping the left fully with a minimal right, and the right fully with a minimal left (two on one side and one on the other, twice), is _sufficient_, and the full left × right product never needs to run. Establishing that bound, and the chain depth that requires the second neighbor, is a **core requirement**: it determines both the testing cost _and_ how far a rule’s conditions may reach.

### Reality check: joint dependence is real, and depth is bounded by what you can afford

Design-imposed separability and the cheap `46³ × 2` basis are a **goal, not a guarantee.** The current position:

- **Approach: aim for locality imposed by the design, and fall back to exhaustive testing.** The author would _like_ the language restricted so that the cheap basis is sufficient by construction. Getting there is “an involved factfinding mission”, and what he wants **may not be possible.** The fallback, which the author accepts, is to **spend CPU on exhaustive testing.**
- **Real joint dependence exists, and it breaks separability.** Example: in ·Utter·Gay·Low·It (“ugly”), the correct ·Gay stance is the one that is joined by ·Utter at the x-height _and_ joins ·Low at the baseline. Each constraint is one-sided (entry from the left, exit to the right), but the **stance is chosen by priority among the _intersection_** of the left-compatible and right-compatible stances. The highest-priority member of an intersection **can’t be computed** from each side’s own highest-priority stance. With a minimal right neighbor, the left sweep would choose a _different, higher-priority_ ·Gay than the two-sided case needs. That is why a context-specific stance like this ·Gay “might be pretty low down on a priority list”: it only wins when both constraints apply together. So simple left/right separability fails wherever contextual priority meets a two-sided constraint.
- **The chain depth is limited by cost, not proven.** Testing two neighbors on each side has found bugs that one on each side missed. Three on each side takes hours to days for even a _single_ test, which is not practical. So depth 2 is the **most that can be afforded**, and its sufficiency is _hoped for_, not proven.

For the rebuild, this means: enforce a **depth bound by construction**, and reject any rule that would reach past the tested window, so that depth-2 testing is provably complete _for the rules that exist_. Then **find separability by region**: run the cheap one-sided sweeps where left and right provably don’t interact, and fall back to the full product only where the dependence is joint, like ·Gay in “ugly”. The build should **flag** which rules require the expensive path, so the cost is visible.

### Whole-word assertions are the cheap, preferred lock where they fit

Many of the Manual’s `data-expect` assertions are word-initial, word-final, or whole-word, which is what whole-word assertions are for. Their advantage is cost: **each needs one render**, while a context-free pair lock needs a large sweep. So whole-word assertions are the natural place for the mandatory tier and for any behavior that can be stated at word scale. The expensive pair-in-context sweep is the fallback when a word-scale assertion can’t capture the intent.

### The corpus is mostly generated nonsense

Most of what is rendered and compared is **generated nonsense**: synthetic character sequences that cover every pair and deeper combinations. This is the separability sweep described above. It must be synthetic because **most of the constraints the author wants to correct never appear in real words.** The Manual’s text and its `data-expect` attributes provide many required constraints, but they are a **minority**: there are far more constraints _outside_ the Manual than in it. Real prose never reaches most of the problem pairs.

Consequences for verification:

- The Manual corpus is the cheap, authoritative **required** subset. The generated corpus is the **exhaustive** bulk that finds the bugs.
- Changes shown by default are therefore mostly _synthetic_ combinations. That is why the review application must handle **hundreds** of decisions in one sitting, and why the success metric (the machine notices, the human only judges) depends on that tool.

## Selection is local and explainable — and the real activity is saying no

**Selection is local and explainable.** At each position, the system picks the highest-priority stance whose two-sided constraints can be satisfied given its neighbors, and every choice can be explained in those terms. Global join maximization is possible in theory but **rejected**, because a real Quikscript writer does not plan far ahead either, and won’t restructure a whole word to make it slightly faster to write. The font should follow how a person writes, which is locally. So a locally best choice that leaves a neighbor slightly worse is _accepted_. The system never rearranges a word for a global optimum.

**“More joins are better” is only a weak tiebreaker.** Among otherwise equal options, the one that joins is preferred, but taste overrides it. ·He·Owe is forbidden only because, joined, it “is ugly and kind of awkward to write”, and the author would never write it that way by hand, even though joins are generally preferred.

### Two levels: opt-in capability, then forbidding within it

The work is _not_ all forbidding, and the base is _not_ permissive. There are **two separate levels**, with opposite defaults:

- **Capability is opt-in.** By default, _no rune can join its neighbors anywhere_. A join is possible only where a stance **explicitly declares** it. This is a positive declaration: nothing joins until you say it can.
- **Within declared capability, the work is forbidding.** _Among the pairs that can join_, the weak “more joins are better” preference applies, and most daily authoring is saying “not this one” to declared-capable joins that turn out ugly or broken.

So “the project has a lot of saying no” is true of the **selection layer**, not the **capability layer**. Global optimization is out of scope at both levels. Knowing that a better global assignment existed “might be nice”, but the author doesn’t expect to act on it.

This changes how the spec is judged. Its quality is mainly **how cleanly it lets the author forbid joins** at the pair, context, and group levels, without adding stances. There are two kinds of forbidding, with very different costs:

- **Broken**: collisions, reaching joins with no acceptor, height mismatches. Objective. The machine _should find these_, so this work shrinks toward zero as detection improves.
- **Ugly**: ·He·Owe and similar pairs. A matter of taste, but _not entirely_ beyond machine checks (see below). The remaining cases have no machine signature. The author finds them by eye in the review workflow and is the only judge.

### Broken is an agent loop you are not in

For at least one workable definition of “broken”, **all** broken joins can be detected automatically, at least in theory. The intended result: broken joins are **fixed by an agent that runs `make test` in a loop, possibly many times, not by the human.** The human leaves the broken-join loop entirely: the machine detects, fixes, retests, and repeats until clean. (**Open task:** define “broken” precisely enough that detection is provably complete. The loop depends on that definition.)

The balance of work has also changed. Ugly joins were very numerous mainly because the font was not fully specified. Now that a complete font exists, ugly is **bounded** (there “might only be a large amount”, not an unbounded amount), while broken is still a large, continuing share. So a strong defect detector with an automated fix loop is very valuable.

### What “broken” means — and the line against “ugly”

“Broken” is defined structurally, without taste: **a join whose rendered geometry violates a structural invariant that can be checked from the bitmap and anchor data.** The working set of invariants (closed, “for future work”):

- **Off-anchor contact**: ink touches or overlaps somewhere other than the anchors.
- **A selected join that is not physically made**: the clearest case. If a stance is chosen _because it claims to join_ (for example, ·Out’s common stance that connects at the x-height), it is broken when, after all extensions and contractions, either (a) the next rune’s ink **does not touch** where ·Out ends, or (b) the next rune was supposed to **switch to a touching bitmap and did not.** A declared join must connect.
- **Height mismatch**: the two attachment heights don’t meet.

Two further points define the system’s approach:

- **Broken is rejected by default, and the author can override it.** If a join the detector calls broken looks fine, it can be **declared OK** as an explicit, recorded exception. In the rebuild, `rebuild/m1-contact-allow.yaml` is the list of these exceptions.
- **The line between broken and ugly is structure versus taste.** _Broken_ asks “does it connect correctly?” _Ugly_ asks “does it look and feel right?”, even when it connects. So **orientation mismatch (the ·No horizontal-versus-vertical case) is _ugly_, not broken**: the machine only _flags_ it, and the author decides. Keeping broken purely structural is what lets its detection be complete and its fixing automatic.

### Some “ugly” has machine signatures

Some kinds of ugliness have detectable signatures and should be flagged by the machine (and sometimes fixed by it), not left to the eye:

- **Off-anchor contact**: two runes touch at a point that is _not_ their anchor points. This almost always calls for an **extension of one or more pixels** to separate them or to route the contact through a real anchor, “unless something unforeseen comes up.” So a fix can be _proposed_ automatically, but “unless something unforeseen” is where the tooling should **ask** instead of applying it silently (see the extension sections).
- **Orientation mismatch**: some runes join best with **horizontal** strokes and look awkward with **vertical** ones; ·No is the standard example. This generalizes the “two near-verticals read as one thick stroke” defect into a property of each rune.

The second case adds a dimension to the capability model: a stance’s join surface includes not only _where_ it attaches (height) but _how_, meaning the **stroke orientation** it needs at that attachment. The model needs that dimension so the machine can flag orientation mismatch instead of leaving it to taste.

<!-- Interview in progress: more sections to come. -->
