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

This font only has Quikscript letters, angled parentheses, and the space character. You’ll want to use it with [Departure Mono][dm], which has all the other characters you’d want.

#### CSS

> [!IMPORTANT]
> Since Abbots Morton Spaceport Sans doesn’t have a `0` glyph, the [`ch`][ch] CSS unit only works if Departure Mono comes before Abbots Morton Spaceport anything in the font stack.

[ch]: https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Values/length#ch

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
  /* Or 'Abbots Morton Spaceport Sans' */
  font-family: 'Departure Mono', 'Abbots Morton Spaceport Mono', monospace;

  /* <https://caniuse.com/?search=font-smooth> isn’t universally supported as of early 2026, but it might be as you read this, so go check */
  -webkit-font-smoothing: none;
  -moz-osx-font-smoothing: unset;
  font-smooth: never;
}
```

### [Typst][tf]

```typst
#set text(
    font: ("Departure Mono", "Abbots Morton Spaceport Mono"),
)
```

[tf]: https://typst.app/docs/reference/text/text/#parameters-font

I’m not sure if the order of these two fonts matters in Typst since (at the time of this writing) it doesn’t have a length measurement that depends on the width of a character like `0`.

### Microsoft Word

If you want to set a fallback font in Microsoft Word you’ll likely need to do something involved and nerdy, like opening up a saved Theme (it’s a .zip file with XML files in it) and editing its long list of language-dependent fallback fonts. Ask an LLM to help you out here. Also ask it to help you convert sizes in pixels to sizes in points.

Or…

### Others

You know how people use [Nerd Fonts][] to get their usual fonts with extra glyphs shoved into them? Maybe you could do that kind of thing and smash either Abbots Morton Spaceport font into Departure Mono.

[nerd fonts]: https://www.nerdfonts.com/

## Wishlist

- Proportional copies of Departure Mono’s punctuation (might as well add its `0` glyph too)
- Quikscript Senior with a gazillion ligatures

## Licensing

SIL OFL 1.1 for the font files themselves, and MIT for everything else.

## Acknowledgements

- [Brad Neil](https://friedorange.xyz/) — Design critique
