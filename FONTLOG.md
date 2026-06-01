# Fontlog for Abbots Morton Spaceport

Much of what goes in a FONTLOG file — other than the changelog — is in the README.

## Changelog

### 15.000

Insectoid aliens and gut punches edition

The bulk of this release’s work is cleaning up a long tail of obviously-incorrect glyph selections. If you tried Abbots Morton Spaceport Sans Senior and thought “nice font, but I’m writing science fiction and it’s screwing up the names of my insectoid aliens that make lots of consonant-heavy chittering noises and use ·Excite and ·Exam in their names, and things don’t look right when my protagonist gets punched in the stomach and makes noises like \*gurk\*”, then this is the release you’ve been waiting for.

According to Claude, this is what’s changed since the last release:

#### Quikscript Senior

- Join into ·He and ·Ye at the baseline:
  - Baseline-exiting letters now connect into ·He and ·Ye at the baseline
  - Join ·Jay·Ye and ·J’ai·Ye at the baseline, extending both J-letters’ ·Ye joins by a pixel
  - Prefer joining to the following letter over the preceding one when ·He or ·Ye could go either way
  - But don’t join ·Jay·He, ·Eat·He, ·Eat·Ye, ·He·Owe, or ·Ye·Owe
  - Don’t let ·He·-ing or ·Ye·-ing join — especially not when ·Thaw follows
- ·It overhaul:
  - Join ·It·Day at the baseline (·It exits at the baseline into a half-·Day) whenever ·It isn’t already taking a baseline join from the letter before it; never join ·It·Day at the x-height, and never across a ZWNJ
  - Stop ·It from joining at the baseline on _both_ sides at once by default; ss04 now also gates ·It’s baseline joins before ·Day and before ·Utter
  - Never join ·It·It or ·It·Exam
  - Don’t join ·Eat·It; stop ·Ye·It from joining at the baseline, even word-initially
  - Connect ·It·Roe at a single height (the x-height)
  - Collapse ·Zoo to its half form after ·It consistently (·Jay·It·Zoo, ·Ye·It·Zoo, ·Eat·It·Zoo, ·It·It·Zoo, and after any tall letter)
  - Let ·Zoo join ·It at the baseline like ·Bay·It and ·Day·It do
  - Extend ·It·Owe (both directions, including ·It·Owe·It), ·It·Cheer, ·It·J’ai, and ·Eight·It each by a pixel; stop contracting ·It·J’ai
- Redraw ·May and retire its ligatures:
  - New ·May glyph
  - Break up the ·Fee·May, ·They·May, and ·At·May ligatures into contextual joins — they’re now two glyphs that connect (at the x-height for ·Fee·May, at the baseline for ·At·May), not single ligated forms
  - Keep ·May·Fee joined at the x-height even when ·Utter follows
  - Keep ·Roe·May joined when ·They+Utter follows, and trim ·May’s dangling exit before an entryless ·They+Utter
  - Make ·Utter·They join again
  - Extend ·May’s exit by a pixel when it follows a baseline join; extend ·May·J’ai by a pixel
- ·Pea joins:
  - Limit ·Pea to joining ·Utter and ·Zoo at the x-height; let ·Pea join ·Et, ·Awe, ·Ox, and ·Oy at the baseline
  - Never join ·Oy·Pea or ·Pea·Owe, and stop letters that would scrape up against ·Pea from joining it
  - Join ·Pea·Pea·May at the baseline
  - Dip half-·Pea’s right leg to the x-height before ·Zoo (contracted by two pixels) and remove the no-ink gap that used to sit below half-·Pea on the right
  - Pick the right ·Pea half after extended ·May and ·Utter exits
- ·Gay:
  - Shorten ·Gay by default and give it a form reachable from the left at the baseline
  - ·Gay·Out joins now
  - Extend ·Gay·Owe by two pixels and ·Gay·J’ai by a lot (no longer contracting it)
  - Extend ·Gay·It and ·Gay·Cheer by a pixel so they stay in contact with the shortened ·Gay
- ·See·Out cleanup:
  - Tidy up ·See·Out·Tea, ·See·Out·Fee, ·See·Out·Oy, and ·See·Out·J’ai
  - Add an alternate ·Out bitmap and use it where it reads better
  - Keep ·See·Out short at the end of a word, and pick the right ·Out when the following letter can’t be joined to
  - Don’t join ·See·Utter or ·See·Excite at the baseline
- Let ·Gay, ·Fee, ·May, ·Roe, ·I, ·Ah, ·Utter, and ·Out join ·Oy at the x-height; keep ·Oy plain after ·Roe·No
- ·Roe:
  - Tighten ·Roe considerably and be pickier about ·Roe·J’ai joins
  - Have ·Roe join to itself, and use the shortened-top ·Roe after ·Jay and ·Eat
  - Extend ·Eat·Roe and ·Owe·Roe by a pixel; contract ·Tea·Roe by a pixel
- ·He:
  - Extend ·He·Exam by a pixel; contract ·He·Owe, tighten ·He·Roe, shorten ·He·Jay, and contract ·He·Zoo a little less
  - Close the one-pixel gaps between half-letters and ·J’ai
- ·Excite: have late ·Excite joins use the reaching-back shape; don’t join ·Roe·Excite, ·No·Excite, ·Zoo·Excite, or ·See·Excite
- ·Why: don’t join ·Why to ·Ah, ·At, ·Eat, ·Exam, ·-ing, ·Low, ·May, ·Ooze, ·Out, or ·Ye; use the dipped ·Why after ·Utter
- Don’t join ·Way·Ooze or ·Way·Tea (even with ss03 on); join ·Way·Roe at the baseline
- Extend ·Ye·I (always, when joined), ·Eight·Ye, ·Eight·He, ·Eight·Exam, and ·Eat·I each by a pixel
- Tighten ·Tea·Zoo and ·He·Zoo by a pixel; make ·Tea·Tea·Zoo and ·Pea·Zoo sensible; stop treating ·Zoo’s descender curl as a broken join
- ·Owe: trim its wings (it’s a pixel narrower); extend ·Owe·Cheer and ·Owe·It by a pixel; stop contracting ·Owe·J’ai
- Have ·Fee reach back to meet ·May, ·No, ·Low, ·Ah, and ·Utter; stop contracting ·Fee·J’ai; let ·See·Utter join its follower at the x-height the way ·Utter does
- Never join ·They·J’ai
- Stop letters that don’t join from influencing each other’s shape choice — a large round of fixes so that a letter in the middle of a broken chain renders the way it does on its own (e.g. ·She stays plain in ·She·Tea·Ah, ·Gay stays plain in ·Gay·It·Ah, ·Owe stays plain before ·May)

#### Not really user-facing, but…

- Shorten compiled glyph form names (e.g. `entry-xheight`/`exit-baseline` become `en-y5`/`ex-y0`), and have every compiled form name say where it joins. This changes the names inside the font file but not how anything renders.

### 14.003

The main reason I’m publishing this is to push out the addition of page numbers to The Manual. Still, there’s:

- ·J’ai-joining cleanup
- ·Excite work
- ·Cheer cleanup
- Tighten ·Zoo·Excite and ·Zoo·No, for both full and half-·Zoo
- Have every ·Fee shape — not just the most common one — reach further into ·Cheer, ·Day, ·Eight, ·Et, ·Foot, ·It, ·Llan, ·No, and ·Roe, pull back from ·J’ai, and (under `ss03`) reach into ·Tea

### 14.002

One can summarize the actual font work as “improve lots of things, but certainly not all of them, and likely break one or two things”. The `Tooling` section is far more understandable and less of an avalanche of minutiae.

That said, I _am_ legitimately proud of my J’ai work; I expect this font to be able to write “Dvořák” with only one penlift by the time I’m done.

#### Quikscript Senior

- ·J’ai joining overhaul:
  - Connect ·Fee, ·May, ·No, ·Roe, ·Low, ·At, ·I, ·Ah, ·Utter, ·Out, and ·Foot into ·J’ai at the x-height
  - Tighten ·Ah·J’ai, ·Utter·J’ai, and ·Out·J’ai by one more pixel
  - Get the ·May·J’ai distance I always wanted
  - Don’t join ·Ooze·J’ai
  - Drop ·J’ai’s baked-in extra-length bitmap; reach is now contextual
  - Have ·J’ai’s exit reach one pixel further before ·-ing, ·Low, ·No, ·Roe, ·See, ·Tea, ·Thaw, and ·Vie
- ·Thaw and ·-ing cleanup:
  - Stop flattening ·She before ·Thaw
  - Don’t join ·Way·Thaw ever
  - Have ·May·Thaw and ·No·Thaw look right when next to ·-ing (no more half-·May or alt-·No dangling under an entryless ·Thaw)
  - Add another pixel of breathing room between ·Thaw·-ing
  - Move ·Thaw’s exit anchor inward by one pixel so following letters land in the right spot
  - Retire the specialized ·Thaw and ·-ing variants that only existed to handle each other
- ·Owe and ·Fee cleanup:
  - Stop ·Owe in word-initial ·Owe·Fee from sprouting a phantom leftward stub
  - Fix ·Owe·Fee·May (and similar ·X-into-ligature shapes) so ·X no longer keeps a forward exit that orphans into the ligature; ·He·Day·Y now picks half-·He at the x-height when ·Day+·Y has no half form
- Don’t join ·They·Jay
- Don’t join ·May·They+Utter, ·No·They+Utter, or ·Foot·They+Utter
- Don’t use half-·Way or half-·Why before ·Vie or ·See
- Trim half-·He’s x-height extension by one pixel (drop the bottom-right pixel of the joining stub)
- Trim ·Roe’s giga-extended joins by one column — tighter into ·Ah, ·At, ·-ing, ·May, ·No, ·See, ·Thaw, ·Vie at baseline and ·Awe, ·J’ai, ·No, ·Ox at the x-height
- ·Excite work:
  - Add a wide-on-both-sides ·Excite for ·It·Excite·Tea-style contexts
  - Pick a wide-on-the-right ·Excite at the start of a word (no leading join)
  - Pick a wide-on-the-left ·Excite before ·Thaw
- Pull ·He.half·Zoo two pixels closer; pull ·Tea.half·Zoo one pixel closer (and stop ·Et·Tea from picking the half form)
- Stop pulling in ·Roe’s long x-height entry stub after ·Ye
- Add a new ss07-gated ·Owe form so ss07 stops accidentally promoting word-initial ·Owe to the entry-having shape

#### Tooling

- `test/the-manual.html` gains `id` attributes for deep-linking to specific passages
- `test/tables.html` keeps the top row sticky, separates the consonant and vowel blocks with thicker borders, stores selected-letter and strip state in the URL hash (so the page is shareable), and puts the focused letter pair into the document `<title>`

### 14.001

#### Quikscript Senior

- Disconnect a long list of ·Ye pairs that shouldn’t have been joining:
  - Don’t join ·Way·Ye, ·He·Ye, ·They·Ye, ·Why·Ye
  - Don’t join ·It·Ye and ·Ye·It
  - Don’t join ·Ye·See, ·Ye·-ing, ·Pea·Ye, ·Tea·Ye
  - Don’t connect ·Ye to ·Excite or ·Exam
  - Extend ·Ye·I so the join reads cleanly
- ·Tea join cleanup:
  - Stop ·Tea·Cheer from joining
  - Add a pixel of breathing room between ·Tea·I in most contexts
  - Lock in that ·Tea gets only one baseline connection (·Excite·Tea stays as an exception)
  - Make ·Out·Tea·Day use a full-size ·Day; prefer ·Tea joins with ·Out in ·Out·Tea·X
- ·Pea join cleanup:
  - Only ·Utter and ·May now join to ·Pea
  - Break up ·Pea·Excite and ·Pea·Exam
- ·Gay changes:
  - Add more ·Gay extensions
  - Don’t join ·Gay·Ooze or ·Gay·Excite
- ·Day-related changes:
  - Make ·Owe·Day never join by default
  - Have ·Way·Day connect at the x-height
  - Have ·He·Day use a half-·Day
- Don’t join ·Why·Thaw; polish up ·Excite·Thaw
- Add `ss05` (allow `·Et ·Tea` to double-join at the baseline)
- Add `ss07` (restore the ·Owe·Day x-height join)
- Prevent letters that don’t join from influencing each other’s shape choice

### 14.000

- Remove `ss01`; ·Utter·Pea joins are now the default
- Remove `ss05`; ·Ox·May now joins at the baseline by default
- Repurpose `ss06` (now: use gapped ·Owe, which doesn't connect at the top)
- Stylistic sets now work in Junior, not just Senior…although that really only applies to `ss06`, knock on wood

### 13.000

Abbots Morton Spaceport Senior now matches, or at least _should_ match, The Manual all the way through to the end. Hopefully I haven’t broken anything.

- Repurpose `ss03` (now: just allow ·Tea to be joined to at the Short height)
  - Add ·Fee·Tea connection at the Short height
- Repurpose `ss04` (now: allow ·It to join at baseline after ·Day and before ·Low)
- Remove `ss06`; ·Roe·Eight now joins at the Short height by default
- Fix ·-ing·Thaw connection
- Fix ·Vie·Low connection
- Fix ·No.alt·Fee pairing
- Improve ·See·Low spacing
- Add ·Day+Utter.half ligature form
- Have ·Ah respond to all ·X+Utter ligatures, not just ·Day+Utter and ·See+Utter

### 12.000

- Fix ·Low and ·Llan (yes, I’ve been writing them wrong _this entire time_)
- Add `ss06` (allow ·Roe·Eight joins at the Short height)
- Remove `ss04`; ·I·May now joins at the Short height by default (use ZWNJ to suppress)
- Remove the `wght` variable-font axis, and…
- Add a Bold variant for Mono, Sans Junior, and Sans Senior
- Rename all font files to include a `-Regular` or `-Bold` suffix

### 11.000

Hoopy Froude edition

11.000 pushes Abbots Morton Spaceport Senior through the end of the Froude-on-Drake passage on page 27 of The Manual.

#### Quikscript Senior

- ZWNJs now do what they’re supposed to in more cases — particularly when the letter after the ZWNJ would otherwise have its form chosen based on a letter preceding the ZWNJ (e.g. the `·Jay ZWNJ ·It` in “Virgin”)
- Don’t use half-·Way before ·Eat
- Don’t use baseline-entry ·It after ·Jay
- Add `ss04` (allow ·I·May join at the Short height)

### 10.001

> Today I’m combining alphabet soup and a laxative.
> I call it “Letter Rip”.

— Skeletor

10.001 updates Abbots Morton Spaceport Senior to mostly match through the Rip van Winkle passage on page 26 of The Manual.

#### Quikscript Senior

- New joining combinations: ·Utter·Pea, ·Utter·Gay, ·Fee·Ye, ·Roe·No, ·May·Pea, ·Oy after ·Low
- Some letters can now connect to a following ·Tea at the Short height (surprisingly, this is A Thing)
- Extend ·It·Zoo connection so ·Zoo doesn’t brush up against the ·It on the way down
- Don’t use half-·Tea before ·Owe
- ZWNJs now correctly break cursive connections before half-letter variants
- ·He·It now kerns tightly, even if there’s a ZWNJ between them (I’m saving 99.99% of the kerning for last, though)
- Add a few stylistic sets to get The Manual to look right — and no, I’m not planning on standardizing their functions until I’m all done, if ever, so don’t expect this to be stable across different fonts:
  - `ss01` (suppress ·Utter·Pea join)
  - `ss02` (allow ·I·Tea join at the Short height)
  - `ss03` (allow ·Tea to be joined to at the Short height)
  - `ss04` (allow ·It to join at baseline after ·Day and before ·Low)
  - `ss05` (allow ·Ox·May join at baseline)

#### Non-Quikscript glyphs

- Add ⚙ (gear)

#### Not really user-facing (assuming I’ve done it right)

- Generate (more) connecting pixels dynamically (instead of having a bunch of special half-·Tea, half-·Pea, and ·Gay bitmaps that have horizontal connections)

### 10.000

#### Quikscript Senior

- Lots of joining improvements and fixes: ·See·Ooze, ·Pea·Pea, ·Tea·See, ·See·Low, ·See·Out, ·May·Zoo, ·May·It, ·Why·It, ·Fee+·Utter, ·See+·At, ·Utter·Fee, and many more
- New glyph variants: joinable ·Jay and ·Why, top-entry ·Fee, wide-stance ·Excite, connects-at-the-baseline-on-both-sides ·It, and ·Jay+Utter ligature
- New joining combinations: ·Tea·Oy, ·See·Low
- Redesign ·Out (big-loop form is now the default proportional form) and ·Oy to match
- Smarter half-letter selection: don’t use half-·Tea before ·Foot or after ·No, don’t use half-·Way before ·It
- Add VS01/VS02 variation selectors for forcing alternate (·Utter, ·No) and half forms (·Pea, ·Tea, etc.), respectively

#### Non-Quikscript glyphs

- Add ⌤ (a proper Enter-key glyph)
- Narrow ⇞ and ⇟
- Fix accented Latin mark shaping

### 9.001

- Move 🌐︎ up by one pixel

### 9.000

- Make the Sans Junior and Sans Senior fonts variable with a `wght` axis (200–800)
  - “Bold” is 800 here (usually it’s 700)
  - weight increases “pixel” _width_, but keeps height the same
  - A weight of 800 will have “pixels” that are twice as wide as they are tall
  - A weight of 600 will have “pixels” that are 1½ times as wide as they are tall
  - A weight of 200 will have “pixels” that are half as wide as they are tall
  - Mono font doesn’t get a bold version
- Add macOS keyboard symbol glyphs: ⌃ ⌅ ⌘ ⌥ ⌦ ⌧ ⌫ ⎋ ⏎ ⏏ ⏻ ␣ 🌐 ↩ ⇞ ⇟ ⇤ ⇥ ⇧ ⇪
- Massive amounts of cleanup and fiddling around

### 8.000

- Make the Senior font match The Manual up through the contractions list on page 17
- Copy a number of glyphs (arrows, boxes, etc.) wholesale from Departure Mono

### 7.000

- Make the Senior font way more connected (even though it’s still a work in progress)

### 6.000

#### Generally

- Completely change the Sans-variant font name by adding “Junior” to the end of “Abbots Morton Spaceport Sans”; this means the old filename is gone

#### Latin letters

- Add kerning so short letters after `f` are tucked in more
- Add kerning so just about everything before a `j` is tucked in more closely

#### Quikscript

- Add enough sans-serif glyphs to have a full-featured font
- Add an alpha-quality very-incomplete proof-of-concepts (yes, plural) Quikscript Senior version of the font with enough ligations (using OpenType’s `curs`) and contextual alternates (·Roe glyphs with an extra-long tail at the top/bottom) and ligatures (·Day·Eat, ·Day·Utter)

### 5.000

- Un-swap ·Oy and ·Out

### 4.000

- Center glyphs within their advance width to match Departure Mono’s metrics
- Add proportional versions of punctuation, etc. from Departure Mono to Abbots Morton Spaceport Sans
- Add the `0` glyph from Departure Mono so the `ch` unit works in CSS

### 3.000

- Have “Abbots Morton Spaceport” refer to the family, “Abbots Morton Spaceport Sans” refer to the proportional variant, and leave “Abbots Morton Spaceport Mono” unchanged

### 2.000

- Add a proportional version
- Fix spacing metrics (it’s actually a monospace font now)
- Improve ·Why

### 1.004

- Improve ·Jay
- Take ·Vie, rotate it 180°, and replace ·Fee…and tweak it further
- Take ·I, flip it, and replace the old ·Eight
- Make the counter of ·Bay 2×2 inside

### 1.003

- Fix sample text
- Improve ·Key
- Improve ·Ah and ·Awe

### 1.002

- Add sample text

### 1.001

- Match Departure Mono’s space width

### 1.000

- Initial release
