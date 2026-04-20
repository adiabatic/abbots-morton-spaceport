# Fontlog for Abbots Morton Spaceport

Much of what goes in a FONTLOG file — other than the changelog — is in the README.

## Changelog

### 14.000

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
