# Abbots Morton Spaceport

[Departure Mono][dm], but [Quikscript][qs].

[dm]: https://departuremono.com/
[qs]: https://www.quikscript.net/

You can get a copy of the latest .otf and .woff2 files from [the Releases page][r].

[r]: https://github.com/adiabatic/abbots-morton-spaceport/releases

## Usage

This font comes in two variants:

- monospace (Abbots Morton Spaceport Mono)
- proportional (Abbots Morton Spaceport Sans)

Either way, you’ll need to:

1. set your fonts to multiples of 11 pixels, probably
1. get [Departure Mono][dm] working, too

### Font sizing

For pixel-perfect rendering on a screen, you’ll want to limit yourself to font sizes that are multiples of 11 **pixels**.

On the other hand, if you’re aiming for print (in, say, Word or Typst), you don’t need to care about pixel alignment if your target is a 600 DPI laser printer.

### Using with Departure Mono

No matter whether you’re using Abbots Morton Spaceport Mono or Sans, you’ll want to use it with [Departure Mono][dm], which has all the other characters you’d want. The Abbots Morton Spaceport family doesn’t have many glyphs beyond Quikscript ones.

If you’re using Abbots Morton Spaceport **Sans**, you’ll want to put it first in your font stack. That way, its proportional punctuation like `·` will get used instead of Departure Mono’s `·` that will have extra space on the sides that you don’t want.

If you’re only using Abbots Morton Spaceport **Mono**, then, as far as I can tell, the order in which you specify fonts doesn’t matter.

#### CSS

```css
@font-face {
  font-family: 'Departure Mono';
  src: url(…/fonts/DepartureMono-Regular.woff2) format('woff2');
}

/* Pick one (or both) */
@font-face {
  font-family: 'Abbots Morton Spaceport Sans';
  src: url(…/fonts/AbbotsMortonSpaceportSans.woff2) format('woff2');
}

@font-face {
  font-family: 'Abbots Morton Spaceport Mono';
  src: url(…/fonts/AbbotsMortonSpaceportMono.woff2) format('woff2');
}

/* you probably know what selector you want already, but we’ll go with :root */
:root {
  /* For Abbots Morton Spaceport Sans, you want it in front so its proportional punctuation gets used */
  font-family: 'Abbots Morton Spaceport Sans', 'Departure Mono', monospace;

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
    // For Abbots Morton Spaceport Sans, you want it in front so its proportional punctuation gets used
    font: ("Abbots Morton Spaceport Sans", "Departure Mono"),
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

## Wishlist

- Proportional copies of Departure Mono’s punctuation
- Special kerning for `fo` and similar sequences where the second letter is round (`e`, `o`, etc.)
- Quikscript Senior with a gazillion ligatures

## Licensing

SIL OFL 1.1 for the font files themselves, and MIT for everything else.

## Acknowledgements

- [Brad Neil](https://friedorange.xyz/) — Design critique
