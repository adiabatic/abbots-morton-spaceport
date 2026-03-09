# Abbots Morton Spaceport

A pixel-font pair (mostly) for [Quikscript][]. Available as a sans-serif version, or you can pair its monospace version with [Departure Mono][] to get a full-featured monospace font.

[departure mono]: https://departuremono.com/
[quikscript]:     https://www.quikscript.net/

You can get a copy of the latest .otf and .woff2 files from [the Releases page][r].

[r]: https://github.com/adiabatic/abbots-morton-spaceport/releases

## Usage

This font comes in two-and-a-half variants:

- proportional (Abbots Morton Spaceport **Sans**)
  - unligated (Abbots Morton Spaceport Sans **Junior**)
  - <del>ligated (Abbots Morton Spaceport Sans **Senior**)</del> — not ready for production use yet (see [Known issues](#known-issues) below)
- monospace (Abbots Morton Spaceport **Mono**)

You’ll definitely need to:

- set your fonts to multiples of 11 pixels (unless you’re targeting print exclusively)

And, if you want a monospace font, you’ll need to:

- get [Departure Mono][] working, too

### Font sizing

For pixel-perfect rendering on a screen, you’ll want to limit yourself to font sizes that are multiples of 11 **pixels**.

On the other hand, if you’re aiming for print (in, say, Word or Typst), you don’t need to care about pixel alignment if your target is a 600 DPI laser printer.

### Using with Departure Mono

While Abbots Morton Spaceport **Sans** is a full-featured font, Abbots Morton Spaceport **Mono** pretty much only supplies Quikscript-only characters. If you want to use Abbots Morton Spaceport in, say, a text editor, you’ll likely want to use it with [Departure Mono][].

Even if you _are_ using Abbots Morton Spaceport Sans, you may also want to use Departure Mono anyway — it has Greek and Cyrillic letters and doesn’t skimp on eastern-European diacritical marks.

#### Font-stack ordering

If you’re using Abbots Morton Spaceport **Sans**, you’ll want to put it first in your font stack. That way, its glyphs will get used instead of Departure Mono’s.

If you’re only using Abbots Morton Spaceport **Mono**, then, as far as I can tell, the order in which you specify fonts doesn’t matter.

#### CSS

```css
@font-face {
  font-family: 'Departure Mono';
  src: url(…/fonts/DepartureMono-Regular.woff2) format('woff2');
}

/* Pick one (or both) */
@font-face {
  font-family: 'Abbots Morton Spaceport Sans Junior';
  src: url(…/fonts/AbbotsMortonSpaceportSansJunior.woff2) format('woff2');
}

@font-face {
  font-family: 'Abbots Morton Spaceport Mono';
  src: url(…/fonts/AbbotsMortonSpaceportMono.woff2) format('woff2');
}

/* you probably know what selector you want already, but we’ll go with :root */
:root {
  /* For Abbots Morton Spaceport Sans, you want it in front */
  font-family: 'Abbots Morton Spaceport Sans Junior', 'Departure Mono', monospace;

  /* For Abbots Morton Spaceport Mono, the same order should be fine */
  font-family: 'Abbots Morton Spaceport Mono', 'Departure Mono', monospace;

  /* <https://caniuse.com/?search=font-smooth> isn’t universally supported as of early 2026, but it might be as you read this, so go check */
  -webkit-font-smoothing: none;
  -moz-osx-font-smoothing: unset;
  font-smooth: never;
}
```

### [Typst][tf]

```typst
#set text(
    // For Abbots Morton Spaceport Sans Junior, you want it in front
    font: ("Abbots Morton Spaceport Sans Junior", "Departure Mono"),
)
```

or:

```typst
#set text(
    // For Abbots Morton Spaceport Mono, the same order should be fine
    font: ("Abbots Morton Spaceport Mono", "Departure Mono"),
)
```

[tf]: https://typst.app/docs/reference/text/text/#parameters-font

### Microsoft Word

If you want to set a fallback font in Microsoft Word you’ll likely need to do something involved and nerdy, like opening up a saved Theme (it’s a .zip file with XML files in it) and editing its long list of language-dependent fallback fonts. Ask an LLM to help you out here. Also ask it to help you convert sizes in pixels to sizes in points.

Or…

### Others

You know how people use [Nerd Fonts][] to get their usual fonts with extra glyphs shoved into them? Maybe you could do that kind of thing and smash either Abbots Morton Spaceport font into Departure Mono.

[nerd fonts]: https://www.nerdfonts.com/

## Weights

Abbots Morton Sans (not Mono) is a variable font. The only variable axis that it has is `wght` — the one used to control how thin/bold the letters are.

Abbots Morton Sans takes the unpopular approach of having the `wght` axis control the _width_ of the pixels that make up the font.

- 200: half-width pixels
- 300: ¾-width pixels
- 400: normal-width pixels
- 600: 1½-width pixels
- 800: double-width pixels

You will probably want to avoid selecting `Bold` in applications when using this; `Bold` is conventionally `700`.

Double-wide pixels are ugly, but they are definitely _bold_. On the other hand, there’s a reason roughly nobody is nostalgic for super-wide fonts from the early days of computing.

Your ability to get not-horrible results from weights other than 400 and 800 depends on your font size and display device. If you’re targeting screens at 100% text zoom (@1x in Apple-speak), then if you want to use half-size pixels or 1½-size pixels, you’ll need to have a font size of 22/44/66…px. If you’re assuming everyone who reads your stuff has a Retina-class display (200% text zoom, @2x) then you’re only going to need to limit yourself to font sizes like 22/44/66px for (and-a-)quarter-pixel weights. Of course, if you’re targeting print with, say, a 600 or a 1200 DPI resolution, then you can probably pick a weight outside these multiples of 100, like `451`, and still be OK.

## Known issues

Abbots Morton Spaceport Sans **Senior** uses OpenType’s `curs` feature. While this is very much the obviously correct OpenType feature to use to join letters to one another as done in Quikscript, support for it in LTRTTB scripts like Latin and Quikscript isn’t universal because so far, `curs` is only used in RTLTTB scripts like Arabic and the one naturally-occurring TTBLTR script, [Mongolian](https://en.wikipedia.org/wiki/Mongolian_script).

In my testing, AMSS Senior works fine in:

- current evergreen browser engines (WebKit (Safari), Gecko (Firefox), Blink (Chrome, Edge))
- Typst (although it does complain about not supporting variable fonts well)

It _should_ work fine in:

- anything that uses [Harfbuzz](https://github.com/harfbuzz/harfbuzz)

It does _not_ work in:

- Microsoft Word 365 (as of February 18, 2026)

## Licensing

SIL OFL 1.1 for the font files themselves, and MIT for everything else.

## Acknowledgements

- [Helena Zhang](https://departuremono.com/) — Inspiration, more than a few directly-copied glyphs, several lightly-changed glyphs, and a handful of glyphs I just drew myself that amusingly happened to look exactly like DM’s
- [Brad Neil](https://friedorange.xyz/) — Design critique
- anyone who’s ever written about fontmaking in public on the Internet — your discussions have been the wind beneath my LLMs’ wings
