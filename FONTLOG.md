# Fontlog for Abbots Morton Spaceport

Much of what goes in a FONTLOG file — other than the changelog — is in the README.

## Changelog

### 17.000

Senior’s joining rules have been rebuilt from scratch on a new engine. The aim was the same font, and mostly it is: no letter looks different on its own, and of the 1,936 two-letter words, 123 are drawn differently. About one in seven three-letter words differs, usually by a single pixel of join length. This is what changed on the way.

Mono is unchanged, and Junior nearly so.

#### All fonts

- Encode ú and ù in Junior and Senior, with the same accents the other accented vowels have (Mono already had them)
- Build every Bold glyph as one merged outline instead of overlapping rectangles, which should get rid of the holes Windows showed in Bold text; shapes and widths are unchanged
- Keep the namer dot raised when a ZWNJ sits between it and a Short letter (Junior and Senior)
- Senior’s files are about five times the size: the OTF goes from 545 KB to 2.8 MB, and the WOFF2 from 120 KB to about 385 KB. Junior’s and Mono’s OTFs are about half their old size.

#### Quikscript Senior — stylistic sets, variation selectors, and ZWNJ

If you set `font-feature-settings` yourself, read this part.

- Remove `ss02`; `·I ~x~ ·Tea` is under `ss03` now
- `ss03`: draw the full-size ·Tea after an x-height join, so it can join onward at the baseline (`·May ~x~ ·Tea ~b~ ·Day`); 16.000 drew a half ·Tea that ended the chain. Also:
  - Keep the ·Out+Tea and ·Tea+Oy ligatures instead of breaking them up to make the join
  - Don’t join `·Owe ~x~ ·Tea`
  - Stop breaking the join on the other side of the letter before ·Tea (`·Gay ~b~ ·May ~x~ ·Tea`)
- `ss04`: let ·It join at the baseline on both sides next to nearly any letter (`·Key ~b~ ·It ~b~ ·See`), not just after ·Day or before ·Day, ·Low, and ·Utter. After ·Pea, ·He, ·No, and ·Utter, ·It keeps its x-height entry instead (`·Pea ~x~ ·It ~b~ ·Low`).
- `ss05`: let ·Tea join at the baseline on both sides after nearly any letter (`·Key ~b~ ·Tea ~b~ ·See`), not just after ·Et
- `ss06`: the gapped ·Owe joins exactly as the plain ·Owe does; in 16.000 it joined nothing
- `ss07`: ·Owe keeps the join from the letter before it (`·Fee ~x~ ·Owe ~x~ ·Day`), and `·Owe ~x~ ·Day` is a pixel longer
- `ss10`: break up every letter, ligatures included; 16.000 left 369 of the two-letter words joined or ligated
- A letter followed by a variation selector stands apart from both neighbors; 16.000 went on to join the shape it selected
- Add VS2 after ·Tea for the half ·Tea
- Treat a ZWNJ as exactly a word boundary: ligatures form after one (`·Pea | ·Tea+Oy`), and a ZWNJ after ·He no longer pulls the next letter into it

#### Quikscript Senior — which letters join

- ·Utter:
  - When ·Utter could join either neighbor at the baseline, join the letter before it: `·Pea ~b~ ·Utter | ·Exam`, where 16.000 had `·Pea | ·Utter.alt ~b~ ·Exam`. This holds after the letters that join ·Utter at the baseline and before ·Excite, ·Exam, ·Eat, ·At, ·I, ·Ah, ·Out, and ·Ooze.
  - Before ·May, join both sides: `·Key ~b~ ·Utter ~x~ ·May`. The Manual’s `·Key | ·Utter.alt ~b~ ·May` takes a ZWNJ after ·Key now.
  - Join `·Utter.alt ~b~ ·Tea`
  - Let ·Fee, ·May, and ·No join the alternate ·Utter at the x-height when it goes on to join at the baseline (`·May ~x~ ·Utter.alt ~b~ ·Tea`), and let `·Utter ~x~ ·Utter.alt` go on to ·Tea, ·Day, ·No, and ·It
  - Don’t join `·{Low,At,I,Ah,Foot} ~x~ ·Utter` when ·Utter goes on to join at the baseline
- ·No:
  - Join `·{Pea,Tea,He} ~b~ ·No.alt` instead of `·{Pea,Tea,He}.half ~x~ ·No`. ·Pea always does, which costs ·No its join to a following x-height letter (`·Pea ~b~ ·No.alt | ·Jay`); ·Tea and ·He keep the x-height join there.
  - Join `·No.alt ~b~ ·Tea`, and `·No.alt ~b~ ·Ye` after a baseline join into ·No
  - Don’t carry a baseline join through ·No.alt into ·Bay, ·They, or ·Way, and don’t join `·See ~b~ ·No.alt`
  - Give `·No ~x~ ·Oy` priority over a baseline join into ·No: `·Bay | ·No ~x~ ·Oy`
  - Join ·No and ·Gay whatever follows ·Gay: `·No ~x~ ·Gay ~b~ ·Tea`
  - Join two ·Nos at the baseline when that lets the word carry on: `·No.alt ~b~ ·No.alt ~b~ ·Tea`
- ·He:
  - Join the full ·He to ·Gay, ·See, ·No, and ·Roe at the baseline; 16.000 didn’t join ·Gay or ·See, and joined ·No and ·Roe at the x-height with a half ·He
  - When ·He could join either neighbor, join the ·Gay or ·See after it: `·Bay | ·He ~b~ ·Gay`
- ·Gay:
  - Join `·Gay ~x~ ·Owe`, `·Gay ~b~ ·Ooze`, and `·Ah ~x~ ·Gay`
  - Don’t join `·Why ~b~ ·Gay`
  - Take the join from a baseline-exiting letter before ·Gay even when ·No or ·Out follows: `·Bay ~b~ ·Gay ~x~ ·No`, `·Bay ~b~ ·Gay | ·Out`
  - After ·May, join ·Gay onward instead: `·May | ·Gay ~b~ ·Vie`
- ·Vie: join `·Vie ~b~ ·Vie` and `·Vie ~b~ ·Exam`; draw ·Vie·Utter as `·Vie ~b~ ·Utter` instead of a ligature (the same pixels, but ·Utter can go on to join ·Tea)
- ·Owe: join `·Low ~x~ ·Owe`, `·Owe ~x~ ·Llan`, and `·Owe ~x~ ·Oy`
- ·Pea: join `·{Et,Awe} ~b~ ·Pea`, which used to overlap without joining; join `·Pea ~6~ ·Pea` before more letters (`·Pea ~6~ ·Pea ~x~ ·Zoo`)
- ·It:
  - Don’t join `·It ~x~ ·Et` or `·At ~x~ ·It`
  - Join into ·It at the baseline and out of it at the x-height before ·Zoo and ·No: `·Key ~b~ ·It ~x~ ·Zoo`, where 16.000 had `·Key | ·It ~b~ ·Zoo.half`
  - Join `·{Tea,He,Owe} ~x~ ·It` when ·It goes on to ·Day, ·I, or ·Utter: `·Tea ~x~ ·It ~b~ ·Day`
  - Don’t join `·They ~x~ ·It` before ·Day, or `·It ~b~ ·Thaw` after ·Pea, ·Tea, ·Fee, or ·He
- ·Fee: when ·Fee could join either neighbor at the x-height, join the letter after it (`·I | ·Fee ~x~ ·Day`); join `·May ~x~ ·Fee` after a baseline join into ·May
- ·They: after ·Utter, let ·They join on at the baseline (`·Utter ~x~ ·They ~b~ ·Day`); after ·I and ·Ah, don’t (`·I ~x~ ·They | ·See`)
- ·May: prefer `·{They,Roe} ~b~ ·May` to ·May’s join with the next letter (`·Roe ~b~ ·May | ·Thaw`); after ·Utter, enter ·May at the x-height and join on (`·Utter ~x~ ·May ~b~ ·Out`)
- ·Roe: join `·{Pea,Tea,He,No,Roe} ~b~ ·Roe ~x~ ·{J’ai,Eight,Awe,Ox,Oy}`, where 16.000 joined into ·Roe at the x-height and stopped; after ·Vie, join ·Roe onward instead (`·Vie | ·Roe ~b~ ·Thaw`)
- ·Tea: keep `·Day ~b~ ·Tea` instead of joining ·Tea onward (`·Day ~b~ ·Tea | ·Vie`); after ·Excite, join ·Tea onward instead (`·Excite | ·Tea ~b~ ·Ah`)
- ·Out and ·Oy: don’t join into an ·Out that goes on to join at the x-height (`·Out | ·Out ~x~ ·Day`), and don’t join ·Oy onward at the baseline once it has been joined at the x-height (`·Gay ~x~ ·Oy | ·Et`)
- Ligatures:
  - Give ·Day+Eat a half-·Day form, so it joins at the baseline wherever ·Day does (`·Bay ~b~ ·Day+Eat`)
  - Let ·They+Zoo join the next letter the way ·Zoo does (`·They+Zoo ~b~ ·Tea`)
  - Space the letter after a ligature the way the ligature’s last letter spaces it
- Stop letters that never joined from touching: `·Ye | ·Gay`, `·Way | ·Fee`, `·Foot | ·Ooze`, and `·Ooze | ·Foot` are a pixel apart now

#### Quikscript Senior — join lengths

- Contract by a pixel:
  - `·{Pea,Bay,Tea,J’ai,-ing,No.alt,It} ~b~ ·Vie`
  - `·{Pea,Bay,Tea,Key,Ye,He,-ing,No.alt,It,Et} ~b~ ·May`
  - `·{Bay,Day,Key,She,Cheer,No.alt,Llan,Awe} ~b~ ·Gay`, and `·Et ~b~ ·Gay` by two
  - `·{Bay,Day,Key,Vie,She,J’ai,Cheer} ~b~ ·No.alt`
  - `·{Tea,Vie,J’ai} ~b~ ·-ing` and `·{Day,Vie} ~b~ ·Excite`
  - `·{Tea,Ye} ~b~ ·Day.half`, `·Key ~b~ ·{Tea,It}`, `·J’ai ~b~ ·{See,Low,Ah}`, and `·See ~b~ ·Low`
  - `·{Pea,Tea,He}.half ~x~ ·Eight`, `·{Tea,He}.half ~x~ ·{Awe,Ox}`, `·May ~x~ ·Roe`, `·It ~x~ ·Jay`, and `·Ah ~x~ ·{Bay,Fee}`
- Extend by a pixel: `·Gay ~x~ ·{Ye,Llan}`, `·{At,Foot} ~x~ ·Owe`, `·Owe ~x~ ·{Et,Owe,Foot}`, `·I ~x~ ·They`, and `·-ing ~b~ ·Ye`; extend `·Gay ~x~ ·Jay` by two
- Take a pixel off ·It’s baseline exit when ·It is entered at the x-height (`·Pea ~x~ ·It ~b~ ·Day`)
- Take a pixel off ·May’s x-height exit into ·Pea, ·Zoo, ·Cheer, ·Ye, ·Llan, and ·Foot when ·May is entered at the baseline

#### Quikscript Senior — letter shapes

- Draw ·I with its smaller loop after ·May, and both loops smaller in `·Eight ~b~ ·I`; give ·I its regular loop back after ·Roe
- Draw ·Eight with its smaller loop before ·Tea
- Keep ·Roe’s far arm at full length when its near arm is lengthened to join: the top arm after ·Jay, ·Eat, and ·Eight, the bottom arm after ·It
- Drop the stub pixel at ·May’s lower left when ·May is entered at the x-height
- Close the one-pixel gap in the `·Utter ~x~ ·Utter.alt` join

#### Quikscript Senior — kerning

- A pixel closer: `·They | ·{See,At}`, `·Ye | ·{Fee,She,J’ai}`, `·He | ·-ing`, `·I | ·Why`, `·Oy | ·Eight`, and `·Owe | ·Thaw`; two pixels closer: `·He | ·{They,She,Cheer}`
- A pixel further apart: `·They | ·Ooze`, `·Fee | ·{Way,Why}`, `·See | ·Why`, `·Zoo | ·Owe`, `·I | ·Ah`, `·Ox | ·Awe`, and `·Owe | ·{Way,Why}`
- Set a half ·Way that joins onward a pixel closer to most letters before it, and a ·See that joins ·Low, ·At, ·Out, or ·Ooze a pixel closer to the letter before it
- Stop ·Thaw touching a ·May, ·Utter, or ·Out that goes on to join the next letter

#### Website

- Add a “Type by letter name” box to the demo page: type `·Day·Utter ·No·It` or `day.utter`, tick stylistic sets, and share the result by URL
- Remove the Kerning page

#### Not really user-facing, but…

- Rename nearly every compiled glyph in Senior, and the alternates in all three fonts (`qsNo.flipped`, `qsUtter.alternate`, `qsOwe.sole.ss06`). This changes the names inside the font files but not how anything renders.
- Drop the glyphs no text could reach from Junior and Mono, Junior’s thirteen unused ligatures among them

### 16.000

Great artists edition

- Automatically copy everything in Departure Mono (currently 1.500) into Abbots Morton Spaceport **Mono**. This includes Latin letters, accented letters, Cyrillic letters, punctuation, symbols, and typographic features.
  - Sans Junior and Senior are untouched by this work, so if you want to write in both Sans for Latin script and Mono for Cyrillic script, things might look weird and mismatched.
- Kern letter pairs in Quikscript Senior — more below.
- Add Shavian letters to Junior and Senior (_not_ Mono) — tell all your Shavian-using friends to try the font out and complain about the ugly letters that I don’t have a good feel for.

Oh, and there’s the usual pile of fixes to letter-pair joins.

Also…

#### Quikscript Senior

- ·It·I now always touches with exactly one connecting pixel instead of butting straight together — word-initially, after a letter that doesn’t join, and after both x-height- and baseline-exiting letters. Mirrors the earlier ·Ye·I fix.
- Stop ·Gay dangling toward a preceding ·May that exits at the x-height and then never joins it. In sequences like ·They·May·Gay·X, ·Gay now falls back to its plain standalone shape instead of leaving a stub poking toward the ·May. A genuine baseline ·May–·Gay join is untouched.

#### Quikscript Senior — kerning

- Senior now sits about a pixel tighter than Junior across the board, and on top of that gets 268 hand-tuned kerning pairs (mostly consonant-first) that pull specific letter combinations 1–4 pixels closer. This is Senior-only — Junior and Mono are unchanged.
- The tightening reaches the “hidden” letter-pair junctions that only ever turn up inside longer words (e.g. ·No staying as its alt form while ·No·Utter is kerned), not just the pairs you can see in isolation. There is much work to be done here in the future, plus kerning “hidden” pairs seem to be another source of “I wanted X to happen, but not in situation Y, too” bugs.

#### Quikscript Senior — stop the dangling

- A big cleanup pass so that a letter in the middle of a sequence it doesn’t join no longer keeps a stray connecting stub poking out toward its neighbor — typically at a word break or just before a ligature that has nowhere to receive the stroke. The letter now falls back to its plain, standalone shape (or keeps just its entry and drops the dangling exit). ·Gay, ·Excite, ·J’ai, ·They, ·May, and ·Utter were the worst offenders, but the fix touches many letters.
- Genuinely new join behavior that came out of the same work:
  - ·Tea·It keeps its x-height join instead of dropping ·Tea to the baseline (two baseline-joined verticals would read as one over-thick stroke), which also fixes ·See·Tea·It·It.
  - ·May no longer drops to its baseline-exit shape before ·He.
  - ·They·Utter joins now.
  - New ·Gay shape for ·Utter·Gay·Tea·Oy and ·Utter·Gay·Thaw·-ing that keeps the ·Utter–·Gay join but drops ·Gay’s now-pointless forward stub.

#### Namer dot (Sans Junior and Sans Senior)

- The naming dot (·) now drops to sit on the x-height in front of a short letter at the start of a name (e.g. ·No, ·It), instead of always riding high. Before tall and deep letters it stays where it was. Mono is unchanged. This doesn’t work in Chromium-based browsers as of this writing.

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

- `site/the-manual.html` gains `id` attributes for deep-linking to specific passages
- `site/tables.html` keeps the top row sticky, separates the consonant and vowel blocks with thicker borders, stores selected-letter and strip state in the URL hash (so the page is shareable), and puts the focused letter pair into the document `<title>`

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
- Repurpose `ss06` (now: use gapped ·Owe, which doesn’t connect at the top)
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
