# Abbots Morton Spaceport

[Departure Mono][dm], but [Quikscript][qs].

[dm]: https://departuremono.com/
[qs]: https://www.quikscript.net/

You can get a copy of the latest .otf and .woff2 files from [the Releases page][r].

[r]: https://github.com/adiabatic/abbots-morton-spaceport/releases

## Usage

This font only has Quikscript letters, angled parentheses, and the space character. You’ll want to use it with [Departure Mono][dm], which has all the other characters you’d want.

Also, for pixel-perfect rendering, you’ll want to limit yourself to font sizes that are multiples of 11 **pixels**.

Contrariwise, if you’re aiming for print (in Word or Typst), you don’t need to care about pixel alignment if your target is a 600 DPI laser printer.

### CSS

```css
font-family: 'Abbots Morton Spaceport Mono', 'Departure Mono', monospace;

/* <https://caniuse.com/?search=font-smooth> isn’t universally supported yet */
-webkit-font-smoothing: none;
-moz-osx-font-smoothing: unset;
font-smooth: never; 
```

### [Typst][tf]

```typst
#set text(
    font: ("Abbots Morton Spaceport Mono", "Departure Mono"),
)
```

[tf]: https://typst.app/docs/reference/text/text/#parameters-font

I don’t think the order of these two fonts matters (yet).

### Microsoft Word

If you want to set a fallback font in Microsoft Word you’ll likely need to do something involved and nerdy, like opening up a saved Theme (it’s a .zip file with XML files in it) and editing its long list of language-dependent fallback fonts. Ask an LLM to help you out here. Also ask it to help you convert sizes in pixels to sizes in points.

Or keep reading…

### Others

You know how people use [Nerd Fonts][] to get their usual fonts with extra glyphs shoved into them? Maybe you could do that kind of thing with this font and Departure Mono.

[nerd fonts]: https://www.nerdfonts.com/

## Wishlist

- Abbots Morton Spaceport Not-Mono (the word “it” looks horrible as-is)
  - I suppose I should put “sans” in the name to make the two trivially distinguishable
- Quikscript Senior with a gazillion ligatures

## Licensing

SIL OFL 1.1 for the font files themselves, and MIT for everything else.

## Acknowledgements

- [Brad Neil](https://friedorange.xyz/) — Design critique
