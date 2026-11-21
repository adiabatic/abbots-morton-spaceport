# Manual letter survey

Every handwritten Quikscript letter in `reference/Quikscript Manual.pdf`, cropped from the scan with its neighbors in view and filed by letter and form.

- `index.html` is the review page: a section per letter, a subsection per form (full, half, alt), and within a form a group per pair of joins (`| ·Pea.half ~x~`).
- `crops/<family>/<form>/<word id>-<letter number>.png` are the crops, at the scan's own resolution.
- `crops.json` describes each crop: the letter, its form and joins, the word it comes from, where the letter sits inside the crop (`letter_x`), the baseline (`baseline_y`), and the Short height (`short`).
- `words/pNN.json` is the source of all three: per PDF page, the handwritten words in reading order and where their letters sit on the scan. `tools/manual_letters_words.py` documents the format.

`tools/manual_letters_prefill.py` writes the word lists from `site/the-manual.html`, and `tools/manual_letters.py build` regenerates `crops/`, `crops.json`, and `index.html` from them. The build recompresses the crops with `oxipng -o max`, so rebuild on a machine that has oxipng or every crop shows as changed. Page numbers are PDF pages throughout; the Manual's printed number is two lower from PDF page 5 on.

## What a word list starts with and what the eye supplies

The prefill takes the letters of each word from the HTML transcription and guesses each letter's form and each join by shaping the word with the pixel font. A word's `src` says how far to trust the guess.

- `expect`: the word's markup has a `data-expect` attribute, copied into `expect`, which was written against the scan and which the pixel font is tested to satisfy. The forms and joins are still the font's, so they match the attribute wherever it asserts something and are a guess wherever it is silent: a letter written without `.alt` or `.half` asserts no form, and two tokens with no operator between them assert a join at no particular height. Change a form or join only when the scan plainly disagrees, and say so in `note`.
  - One attribute can cover several words of a table cell (`won = w/u/n`), and then each of those words carries the whole string.
- `font`: nothing backs the guess. Judge every form and join from the scan.

The positions are always supplied by eye: the page's `lines`, and each word's `line`, `x`, and `cuts`.

## Reading the scan

Coordinates are pixels of the page's embedded scan, origin at the top left. `tools/manual_letters.py view` draws a region with a ruler along the top and left: a tick every 10 pixels, and labeled grid lines whose spacing follows the scale (every 50 pixels below `--scale 2`, every 20 from 2, every 10 from 4, with a faint line every 10 from 2 up). Without `--scale` a view fills about 1,450 pixels, up to a scale of 4. With `--boxes` it draws what the word list records on top, and `--mark-x` and `--mark-y` draw a red line at each coordinate given, to test a reading before recording it.

```sh
uv run tools/manual_letters.py view 16 -o tmp/ml/p16/page.png                              # whole page, to find the handwriting lines
uv run tools/manual_letters.py view 16 --y 490 700 --x 150 1150 -o tmp/ml/p16/a.png         # a stretch of a line, to see its words
uv run tools/manual_letters.py view 16 --y 500 680 --x 380 760 -o tmp/ml/p16/b.png          # two or three words, to read coordinates
uv run tools/manual_letters.py view 16 --y 500 680 --x 380 760 --mark-x 424 452 --mark-y 606 -o tmp/ml/p16/b.png
uv run tools/manual_letters.py batch tmp/ml/p16/line2.txt                                  # record what was read
uv run tools/manual_letters.py view 16 --y 490 700 --x 150 1150 --boxes -o tmp/ml/p16/a.png # check it
```

A batch file holds one command per text line, without the `uv run tools/manual_letters.py` prefix:

```text
line 16 2 --base 150 606 1800 606 --short 33
set p16-w005 --line 2 --x 396 483 --cuts 424 452
set p16-w010 --line 2 --x 1494 1549 --cuts 1511 --joins x --note "the top of ·He overhangs ·Eight"
```

A view about 1,000 scan pixels wide shows which word is which but is too coarse to read a cut from. Read coordinates from views 300 to 500 pixels wide, which come out at a scale near 3 with a labeled line every 20 pixels; one such view per two or three words is the unit of work. Take in at least 130 pixels above the baseline and 80 below, so Tall and Deep letters are whole and a `--boxes` view has room for its names, and let neighboring views overlap by a word. Read a coordinate off the labeled grid lines, never off a position in the image; `--mark-x` and `--mark-y` confirm it. A whole-page view is for finding lines only, since at that scale the end of a line is easy to misjudge.

Whether two letters join or only touch shows at `--scale 8` on a view about 150 pixels wide. They join when the pen runs out of one into the other. Where the ink touches but each stroke keeps its own end, record `|` and say so in a note.

A namer dot is a raised dot at mid-height before a name. It is not a letter and gets no cell, and a mark under it on the baseline can be the tail of the letter that follows.

## What to record

- A line (`line PAGE N --base X_LEFT Y_LEFT X_RIGHT Y_RIGHT --short H`) is one row of handwriting, numbered from the top of the page. Letters that stand alone inside a typed sentence form a line of their own.
  - The baseline is the lower of the two parallels, the one Short letters stand on; give its y near the line's left end and near its right end. Tails of Deep letters go below it.
  - `short` is the distance from the baseline up to the upper parallel, where Short letters top out. Tall letters rise well above it, to about twice that height in this hand, and Deep letters drop about as far below the baseline. Where a line has no Short letter to measure, take half the height of a Tall or Deep letter.
  - A word that sits more than about 6 pixels off its line's baseline takes its own `--base Y`.
- A word (`set ID --line N --x X0 X1 --cuts …`) takes the x of its leftmost and rightmost ink and one cut between each pair of neighboring letters, so a word of three letters has two cuts.
  - Put a cut in the middle of the joining stroke, or in the middle of the gap when the letters do not touch. When one letter overhangs the next, cut where the main body of the first ends and add a `--note`.
  - A step (the small zig-zag the Manual puts between some halved letters) belongs to the join: cut through the middle of it.
  - A half letter's cell runs to where the next letter's own stroke starts, even when the half letter's stem carries on past the upper parallel into the join.
  - Aim to be within about 5 pixels.
- Forms (`--letters`, always the whole word): `qsPea` is the full letter, `qsPea.half` a half letter, `qsNo.alt` an alternative. Section II of the Manual (PDF pages 16 and 17) defines them: a half letter is the top half of a Tall letter or the part of ·Way below the line, or ·Day or ·Zoo cut short; the alternatives are ·No turned upside down and ·Utter reversed.
- Joins (`--joins`, always the whole word): `b` for a join along the baseline, `x` for one at the upper parallel, `t` for one at the top of two Tall letters, `|` where the pen lifts. `6` is the pixel font's code for a join one row above the upper parallel; record it only where the prefill has it and the scan agrees.
  - `+` marks a pair the pixel font draws as one ligature, whatever the forms of its letters. Keep it when the pair is joined, and replace it with `|` when the pen plainly lifts.
- `--gloss` is the orthodox word, where the page prints one above or beside the handwriting and the list has none.
- `--note` holds anything a reader of the crop should know: a blot, a correction, a doubtful reading, a form drawn unlike its siblings.

The typed colons, dots, and equals signs that separate examples are not letters. A word the transcription has but the scan lacks keeps `x` empty and takes a note saying so.

## Checking

Every stretch gets a second view with `--boxes` after its commands run. The tinted cells alternate from letter to letter, each with its name above; the green line is the baseline and the orange line the upper parallel; a red join code sits under each cut, and the word's number under its left edge. Read every name against the shape under it (PDF page 2 is the alphabet chart), and move any cut, edge, or baseline that is off.

`uv run tools/manual_letters.py check` validates the structure of every word list and counts the words still without a position.
